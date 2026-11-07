# Tests for the `stopline part` commands in a real git repository (spec sections 8, 13.1, 13.3).
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner, Result

from stopline.cli import app as app_module
from stopline.cli import part as part_module
from stopline.cli.app import app, main
from stopline.core import trust
from stopline.core.contract import approval_matches, load_contract
from stopline.core.models import Status
from stopline.store.db import open_state

runner = CliRunner()

DRAFT = """\
schema  = 1
title   = "Billing"
summary = "Monthly subscriptions"
paths   = ["src/billing/**"]

[[items]]
id        = "invoice"
desc      = "Create an invoice"
anchors   = ["route:POST /invoices"]
done_when = ["pytest", "-k", "a  b"]
"""


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True)  # noqa: S603, S607


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository with one commit, as the current folder, ignoring this machine's git settings."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("STOPLINE_ALLOW_NONINTERACTIVE", raising=False)
    root = tmp_path / "project"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Test Owner")
    _git(root, "config", "user.email", "owner@example.test")
    _git(root, "commit", "-q", "--allow-empty", "-m", "first")
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pass the owner-only check the way a script set up by the owner does."""
    monkeypatch.setenv("STOPLINE_ALLOW_NONINTERACTIVE", "1")


def _run(*arguments: str, stdin: str | None = None) -> Result:
    return runner.invoke(app, ["part", *arguments], input=stdin)


def _draft(root: Path, part_id: str = "billing", text: str = DRAFT) -> Path:
    source = root / f"{part_id}.draft.toml"
    source.write_text(text.replace("src/billing/", f"src/{part_id}/"), encoding="utf-8")
    assert _run("new", part_id, "--from", str(source)).exit_code == 0
    return root / ".stopline" / "parts" / f"{part_id}.toml"


def _active(root: Path, part_id: str = "billing") -> Path:
    path = _draft(root, part_id)
    assert _run("approve", part_id).exit_code == 0
    assert _run("start", part_id, "--allow-dirty").exit_code == 0
    return path


def _complete(path: Path) -> None:
    """Complete a part the way the end-of-turn check will (spec section 8)."""
    state = open_state(path.parent.parent)
    try:
        trust.complete(state.connection, path, "0" * 40, datetime.now(UTC))
    finally:
        state.connection.close()


def _active_as_owner(root: Path) -> Path:
    """An active part set up by the owner; later commands run without the owner check."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("STOPLINE_ALLOW_NONINTERACTIVE", "1")
        return _active(root)


def test_whole_journey(repo: Path, owner: None) -> None:
    """Spec Appendix C: draft, approve, start, keep, and look at the result."""
    path = _draft(repo)
    assert load_contract(path).status is Status.DRAFT

    assert _run("approve", "billing").exit_code == 0
    contract = load_contract(path)
    assert contract.status is Status.APPROVED
    assert contract.approval is not None
    assert contract.approval.approved_by == "Test Owner"
    assert approval_matches(contract)

    assert _run("start", "billing", "--allow-dirty").exit_code == 0
    assert load_contract(path).status is Status.ACTIVE

    kept = _run("keep", "function:billing.tax", "--reason", "Needed by invoices")
    assert kept.exit_code == 0
    assert "Kept in billing" in kept.output
    assert approval_matches(load_contract(path))

    shown = _run("show", "billing")
    assert shown.exit_code == 0
    assert "billing: Billing [active]" in shown.output
    assert "runs: pytest -k 'a  b'" in shown.output
    assert "function:billing.tax: Needed by invoices" in shown.output
    assert "Approval: valid, by Test Owner" in shown.output

    listed = _run("list")
    assert listed.exit_code == 0
    assert "billing  active  1 items, 1 extras" in listed.output


def test_new_from_template_warns_and_says_what_is_next(repo: Path) -> None:
    """FR-CON-06: `part new` shows the warning; an agent can run this without a terminal."""
    result = _run("new", "billing")

    assert result.exit_code == 0
    assert "can never be verified" in result.output
    assert "stopline part approve billing" in result.output


def test_new_works_from_a_subfolder(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec section 15.1: contracts live at the top of the repository wherever you stand."""
    (repo / "src").mkdir()
    monkeypatch.chdir(repo / "src")

    assert _run("new", "billing").exit_code == 0

    assert (repo / ".stopline" / "parts" / "billing.toml").exists()


@pytest.mark.parametrize(
    ("command", "name"),
    [
        (("approve", "billing"), "part approve"),
        (("pause",), "part pause"),
        (("reopen", "billing"), "part reopen"),
        (("abandon", "billing"), "part abandon"),
    ],
)
def test_owner_only_commands_refuse_without_a_terminal(
    repo: Path, command: tuple[str, ...], name: str
) -> None:
    """FR-LC-03, spec section 13.1: an agent's shell has no terminal."""
    path = _draft(repo)
    before = path.read_bytes()

    result = _run(*command)

    assert result.exit_code == 4
    assert f"`stopline {name}` can only be run by the owner" in result.output
    assert path.read_bytes() == before


def test_keep_needs_no_terminal(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-CON-04: an agent can run it; the owner's prompt comes from the hook in M6."""
    monkeypatch.setenv("STOPLINE_ALLOW_NONINTERACTIVE", "1")
    path = _active(repo)
    monkeypatch.delenv("STOPLINE_ALLOW_NONINTERACTIVE")

    result = _run("keep", "function:a.b", "--reason", "Helper")

    assert result.exit_code == 0
    assert load_contract(path).approved_extras[0].feature == "function:a.b"


def test_keep_without_a_reason_is_a_usage_error_for_an_agent(repo: Path, owner: None) -> None:
    """Spec section 9.2, 13.1: a reason is required, and only a person can be prompted."""
    path = _active(repo)

    result = _run("keep", "function:a.b")

    assert result.exit_code == 4
    assert load_contract(path).approved_extras == []


def test_keep_prompts_for_a_reason_at_a_terminal(
    repo: Path, owner: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec section 13.1: a person at a terminal is asked."""
    path = _active(repo)
    monkeypatch.setattr(part_module, "_is_terminal", lambda: True)

    result = _run("keep", "function:a.b", stdin="Because\n")

    assert result.exit_code == 0
    assert load_contract(path).approved_extras[0].reason == "Because"


def test_keep_with_nothing_active(repo: Path, owner: None) -> None:
    """Spec section 13.1: keep names no part, so there must be an active one."""
    _draft(repo)

    result = _run("keep", "function:a.b", "--reason", "Helper")

    assert result.exit_code == 3
    assert "No part is active" in result.output
    assert "--part ID" in result.output


def test_keep_in_a_completed_part_needs_no_reopen(repo: Path, owner: None) -> None:
    """Spec section 13.1: a feature is often kept just after its part completes (12.1)."""
    path = _active(repo)
    _complete(path)

    result = _run("keep", "function:a.b", "--part", "billing", "--reason", "Helper")

    assert result.exit_code == 0
    contract = load_contract(path)
    assert contract.status is Status.COMPLETE
    assert contract.approved_extras[0].feature == "function:a.b"
    assert approval_matches(contract)


def test_keep_can_name_a_part_that_is_not_the_active_one(repo: Path, owner: None) -> None:
    """Spec section 13.1: --part wins over the active part."""
    first = _active(repo, "first")
    _complete(first)
    second = _active(repo, "second")

    assert _run("keep", "function:a.b", "--part", "first", "--reason", "Helper").exit_code == 0

    assert load_contract(first).approved_extras[0].feature == "function:a.b"
    assert load_contract(second).approved_extras == []


@pytest.mark.parametrize("part", ["nope", "../evil"])
def test_keep_with_a_bad_part_name(repo: Path, part: str) -> None:
    """Spec section 13.3: a part that does not exist, or cannot exist, exits 3."""
    result = _run("keep", "function:a.b", "--part", part, "--reason", "Helper")

    assert result.exit_code == 3


def test_keep_refuses_a_contract_edited_since_approval(repo: Path, owner: None) -> None:
    """FR-CON-03: an agent cannot use keep to sign its own edit."""
    path = _active(repo)
    path.write_text(
        path.read_text(encoding="utf-8").replace("src/billing", "src"), encoding="utf-8"
    )

    result = _run("keep", "function:a.b", "--reason", "Helper")

    assert result.exit_code == 3
    assert "changed since it was approved" in result.output


def test_approve_shows_the_commands_and_asks_at_a_terminal(
    repo: Path, owner: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec Appendix C, 17.2: the owner sees what they approve, including commands to be run."""
    path = _draft(repo)
    monkeypatch.setattr(part_module, "_is_terminal", lambda: True)

    declined = _run("approve", "billing", stdin="n\n")
    assert declined.exit_code == 0
    assert "runs: pytest -k 'a  b'" in declined.output
    assert "Nothing changed" in declined.output
    assert load_contract(path).status is Status.DRAFT

    accepted = _run("approve", "billing", stdin="y\n")
    assert accepted.exit_code == 0
    assert load_contract(path).status is Status.APPROVED


def test_approve_stops_if_the_file_changes_during_review(
    repo: Path, owner: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-CON-03: an edit made while the owner reads the screen must not get signed."""
    path = _draft(repo)
    monkeypatch.setattr(part_module, "_is_terminal", lambda: True)

    def edit_while_reviewing(*_: object) -> bool:
        path.write_text(
            path.read_text(encoding="utf-8").replace("src/billing/**", "**"), encoding="utf-8"
        )
        return True

    monkeypatch.setattr(typer, "confirm", edit_while_reviewing)

    result = _run("approve", "billing")

    assert result.exit_code == 3
    assert "changed while you were reviewing" in result.output
    assert load_contract(path).status is Status.DRAFT
    assert load_contract(path).approval is None


def test_hidden_characters_are_shown_as_codes(repo: Path, owner: None) -> None:
    """Spec section 17.2: terminal codes and invisible text cannot disguise a contract."""
    text = DRAFT.replace("Create an invoice", "Fine\\u001b[2K\\u200b").replace(
        '["pytest", "-k", "a  b"]', '["evil\\u001b[8m"]'
    )
    _draft(repo, text=text)

    result = _run("approve", "billing")

    assert result.exit_code == 0
    assert "\x1b" not in result.output
    assert chr(0x200B) not in result.output
    assert "\\x1b[2K" in result.output
    assert "\\u200b" in result.output


def test_show_says_when_a_contract_was_edited(repo: Path, owner: None) -> None:
    """FR-CON-03: an edited contract is reported, not shown as approved."""
    path = _active(repo)
    path.write_text(path.read_text(encoding="utf-8").replace("Monthly", "Yearly"), encoding="utf-8")

    result = _run("show", "billing")

    assert result.exit_code == 0
    assert "NOT VALID" in result.output


def test_show_a_draft_lists_its_extras_and_overlap(repo: Path) -> None:
    """Spec section 13.1: nothing that matters is left off the screen."""
    text = DRAFT.replace(
        'paths   = ["src/billing/**"]', 'paths   = ["src/billing/**"]\nallow_overlap = true'
    ).replace(
        'done_when = ["pytest", "-k", "a  b"]', 'done_when = { cmd = "pytest", shell = true }'
    )
    _draft(repo, text=text)

    result = _run("show", "billing")

    assert "may be shared" in result.output
    assert "runs: pytest (in a shell)" in result.output
    assert "anchor: route:POST /invoices" in result.output
    assert "Approval: none" in result.output


def test_list_with_no_parts(repo: Path) -> None:
    """Spec section 13.2: an empty list says how to begin."""
    result = _run("list")

    assert result.exit_code == 0
    assert "stopline part new ID" in result.output


def test_start_refuses_uncommitted_changes_without_the_flag(repo: Path, owner: None) -> None:
    """Spec section 8: --allow-dirty is the only way past uncommitted work."""
    path = _draft(repo)
    assert _run("approve", "billing").exit_code == 0
    (repo / "src" / "billing").mkdir(parents=True)
    (repo / "src" / "billing" / "a.py").write_text("x = 1\n", encoding="utf-8")

    result = _run("start", "billing")

    assert result.exit_code == 3
    assert "src/billing/a.py" in result.output
    assert load_contract(path).status is Status.APPROVED

    assert _run("start", "billing", "--allow-dirty").exit_code == 0


def test_pause_reopen_and_abandon(repo: Path, owner: None) -> None:
    """Spec section 8: the remaining stage moves, through the commands."""
    path = _active(repo)

    paused = _run("pause")
    assert paused.exit_code == 0
    assert "Paused: billing" in paused.output
    assert load_contract(path).status is Status.APPROVED

    assert _run("start", "billing", "--allow-dirty").exit_code == 0
    _complete(path)
    assert _run("reopen", "billing").exit_code == 0
    assert load_contract(path).status is Status.ACTIVE

    assert _run("abandon", "billing").exit_code == 0
    assert load_contract(path).status is Status.ABANDONED


@pytest.mark.parametrize(
    "command",
    [
        pytest.param(("show", "nope"), id="missing part"),
        pytest.param(("show", "../evil"), id="id leaves the folder"),
        pytest.param(("new", "Bad_ID"), id="bad id"),
        pytest.param(("start", "nope"), id="start a missing part"),
    ],
)
def test_contract_errors_exit_3(repo: Path, command: tuple[str, ...]) -> None:
    """Spec section 13.3: contract problems exit 3 with a fix and no traceback."""
    result = _run(*command)

    assert result.exit_code == 3
    assert "Fix: " in result.output
    assert "Traceback" not in result.output


def test_broken_contract_exits_3_from_list(repo: Path) -> None:
    """Spec section 13.3: one broken file is reported by name."""
    parts = repo / ".stopline" / "parts"
    parts.mkdir(parents=True)
    (parts / "bad.toml").write_text("title =", encoding="utf-8")

    result = _run("list")

    assert result.exit_code == 3
    assert "bad.toml" in result.output


def test_outside_a_repository_exits_5(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec section 13.3: not a git repository is an environment error."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))

    result = _run("list")

    assert result.exit_code == 5
    assert "git init" in result.output


def test_approve_without_a_git_name_exits_5(repo: Path, owner: None) -> None:
    """Spec section 8: the approver is git user.name, so it must exist."""
    path = _draft(repo)
    _git(repo, "config", "--unset", "user.name")

    result = _run("approve", "billing")

    assert result.exit_code == 5
    assert "git config user.name" in result.output
    assert load_contract(path).status is Status.DRAFT


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param(["part"], id="no subcommand"),
        pytest.param(["part", "nonsense"], id="unknown subcommand"),
        pytest.param(["part", "show"], id="missing id"),
    ],
)
def test_usage_errors_exit_4(arguments: list[str]) -> None:
    """Spec section 13.3: bad arguments exit 4."""
    assert runner.invoke(app, arguments).exit_code == 4


def test_unexpected_error_exits_70_and_says_where_to_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Spec section 13.3: a bug exits 70 and the message says how to report it."""

    def broken() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(app_module, "app", broken)

    with pytest.raises(SystemExit) as caught:
        main()

    assert caught.value.code == 70
    captured = capsys.readouterr().err
    assert "boom" in captured
    assert "github.com/Webs4Dev/stopline/issues" in captured


# Trust records through the real commands (FR-CON-08, FR-CON-09, spec 15.2).


def _state_db(root: Path) -> Path:
    return root / ".stopline" / "state.db"


def _forget_trust(root: Path) -> None:
    """What a fresh clone, or a contract arriving from another branch, looks like."""
    state = open_state(root / ".stopline")
    state.connection.execute("DELETE FROM trust")
    state.connection.close()


def test_start_refuses_a_contract_this_clone_never_trusted(repo: Path, owner: None) -> None:
    """FR-CON-09: a signed contract from elsewhere cannot be started without the owner."""
    path = _draft(repo)
    assert _run("approve", "billing").exit_code == 0
    _forget_trust(repo)
    before = path.read_bytes()

    result = _run("start", "billing", "--allow-dirty")

    assert result.exit_code == 3
    assert "not trusted" in result.output
    assert "stopline part approve billing" in result.output
    assert path.read_bytes() == before


def test_an_agent_cannot_trust_a_contract_by_keeping_a_feature(repo: Path) -> None:
    """FR-CON-09: `part keep` needs no terminal, so it must not create trust either."""
    path = _active_as_owner(repo)
    _forget_trust(repo)

    result = _run("keep", "function:a.b", "--reason", "Helper")

    assert result.exit_code == 3
    assert not load_contract(path).approved_extras
    assert _run("start", "billing", "--allow-dirty").exit_code == 3  # still untrusted


def test_hand_edited_status_is_refused_until_the_owner_approves(repo: Path, owner: None) -> None:
    """FR-CON-08: status is outside the approval hash, so the trust record catches the edit."""
    path = _active(repo)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('status = "active"', 'status = "approved"'), encoding="utf-8")

    refused = _run("abandon", "billing")
    assert refused.exit_code == 3
    assert "status (trusted: active, file: approved)" in refused.output

    assert _run("approve", "billing").exit_code == 0
    assert _run("abandon", "billing").exit_code == 0


def test_every_contract_changing_command_keeps_trust(repo: Path, owner: None) -> None:
    """FR-LC-01: after each Stopline command the file and the record still agree."""
    path = _active(repo)
    for arguments in (
        ["keep", "function:a.b", "--reason", "Helper"],
        ["pause"],
        ["start", "billing"],
        ["abandon", "billing"],
    ):
        assert _run(*arguments).exit_code == 0, arguments
        state = open_state(repo / ".stopline")
        assert trust.is_trusted(state.connection, load_contract(path)), arguments
        state.connection.close()


def test_a_damaged_database_is_replaced_with_a_warning(repo: Path, owner: None) -> None:
    """Spec section 15.2: a rebuild leaves every part untrusted, and the owner is told."""
    path = _active(repo)
    _state_db(repo).write_bytes(b"not a database" * 100)

    result = _run("keep", "function:a.b", "--reason", "Helper")

    assert "was damaged and has been replaced" in result.output
    assert "stopline trust" in result.output
    assert result.exit_code == 3  # the new database holds no trust records
    assert not load_contract(path).approved_extras


def test_a_database_error_during_a_command_exits_5(
    repo: Path, owner: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec section 13.3: a database that fails after opening is an environment error."""
    _active(repo)

    def failing(*arguments: object, **options: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(part_module, "trusted_change", failing)

    result = _run("keep", "function:a.b", "--reason", "Helper")

    assert result.exit_code == 5
    assert "disk I/O error" in result.output
    assert "delete .stopline/state.db" in result.output


# `stopline trust` (FR-CON-09, spec 13.1).


def _trust(*arguments: str, stdin: str | None = None) -> Result:
    return runner.invoke(app, ["trust", *arguments], input=stdin)


def _signed_in_another_clone(root: Path, part_id: str = "billing") -> Path:
    """A signed contract with no trust record here, as after a fresh clone."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("STOPLINE_ALLOW_NONINTERACTIVE", "1")
        path = _draft(root, part_id)
        assert _run("approve", part_id).exit_code == 0
    _forget_trust(root)
    return path


def test_trust_shows_the_commands_then_trusts(repo: Path, owner: None) -> None:
    """FR-CON-09: every done_when command is shown before the contract is trusted."""
    _signed_in_another_clone(repo)

    result = _trust()

    assert result.exit_code == 0
    assert result.output.index("runs: pytest -k 'a  b'") < result.output.index("Trusted: billing")
    assert _run("start", "billing", "--allow-dirty").exit_code == 0


@pytest.mark.parametrize(("answer", "trusted"), [("n\n", False), ("y\n", True)])
def test_trust_at_a_terminal_asks_first(
    repo: Path, owner: None, monkeypatch: pytest.MonkeyPatch, answer: str, trusted: bool
) -> None:
    """FR-CON-09: the owner decides after seeing the commands."""
    path = _signed_in_another_clone(repo)
    monkeypatch.setattr(part_module, "_is_terminal", lambda: True)

    result = _trust(stdin=answer)

    assert result.exit_code == 0
    assert "Their `runs` commands may then run" in result.output
    state = open_state(repo / ".stopline")
    assert trust.is_trusted(state.connection, load_contract(path)) is trusted
    state.connection.close()


def test_an_agent_cannot_run_trust(repo: Path) -> None:
    """FR-CON-09 and spec 17.3: trust is owner-only, so it needs a terminal."""
    _signed_in_another_clone(repo)

    result = _trust()

    assert result.exit_code == 4
    assert "only be run by the owner" in result.output


def test_trust_refuses_a_contract_edited_since_approval(repo: Path, owner: None) -> None:
    """FR-CON-03: trusting cannot approve edits; that is `part approve`'s job."""
    path = _signed_in_another_clone(repo)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("Monthly subscriptions", "Everything"), encoding="utf-8")

    result = _trust()

    assert result.exit_code == 3
    assert "stopline part approve billing" in result.output
    assert _run("start", "billing", "--allow-dirty").exit_code == 3


def test_trust_skips_drafts_and_trusts_only_named_parts(repo: Path, owner: None) -> None:
    """Spec 13.1: IDs pick parts; a draft has nothing approved to trust."""
    _signed_in_another_clone(repo, "first")
    _signed_in_another_clone(repo, "second")
    _draft(repo, "third")

    assert "Skipped third: it is a draft" in _trust().output
    _forget_trust(repo)
    result = _trust("second")

    assert result.exit_code == 0
    assert "Trusted: second" in result.output
    assert "first" not in result.output
    assert _run("start", "first", "--allow-dirty").exit_code == 3


def test_trust_with_base_skips_contracts_changed_since_then(repo: Path, owner: None) -> None:
    """Spec 12.3: CI trusts only contracts unchanged from the base, so a PR cannot run its own."""
    _signed_in_another_clone(repo, "kept")
    changed = _signed_in_another_clone(repo, "changed")
    _git(repo, "add", ".stopline")
    _git(repo, "commit", "-q", "-m", "contracts")
    _git(repo, "tag", "base")
    # The pull request edits one contract, re-signs it, and adds a new one.
    text = changed.read_text(encoding="utf-8")
    changed.write_text(text.replace("Monthly subscriptions", "Run anything"), encoding="utf-8")
    assert _run("approve", "changed").exit_code == 0
    _signed_in_another_clone(repo, "added")
    _forget_trust(repo)

    result = _trust("--base", "base")

    assert result.exit_code == 0
    assert "Trusted: kept" in result.output
    assert "Not trusted: changed changed since base." in result.output
    assert "Not trusted: added changed since base." in result.output
    assert "Run anything" not in result.output


def test_trust_with_an_unknown_base_is_an_environment_error(repo: Path, owner: None) -> None:
    """Spec 13.3: a base git cannot find is an environment error, not "nothing changed"."""
    _signed_in_another_clone(repo)

    result = _trust("--base", "--not-a-ref")

    assert result.exit_code == 5
    assert "fetch it first" in result.output


def test_trust_refuses_a_contract_changed_during_review(
    repo: Path, owner: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-CON-09: what gets trusted is exactly what the owner saw."""
    path = _signed_in_another_clone(repo)
    monkeypatch.setattr(part_module, "_is_terminal", lambda: True)

    def edit_while_reviewing(*arguments: object, **options: object) -> bool:
        path.write_text(path.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
        return True

    monkeypatch.setattr(typer, "confirm", edit_while_reviewing)

    result = _trust()

    assert result.exit_code == 3
    assert "changed while you were reviewing" in result.output
    state = open_state(repo / ".stopline")
    assert trust.read_record(state.connection, "billing") is None
    state.connection.close()


def test_pause_with_nothing_active(repo: Path, owner: None) -> None:
    """Spec section 8: there is nothing to pause, which is a contract problem (exit 3)."""
    _draft(repo)

    result = _run("pause")

    assert result.exit_code == 3
    assert "No part is active" in result.output


def test_trust_with_only_drafts_trusts_nothing(repo: Path, owner: None) -> None:
    """FR-CON-09: a draft has no approval to trust; the owner is told so."""
    _draft(repo)

    result = _trust()

    assert result.exit_code == 0
    assert result.output.endswith("Nothing to trust.\n")
