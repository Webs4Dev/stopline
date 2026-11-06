# Tests for the `stopline part` commands in a real git repository (spec sections 8, 13.1, 13.3).
import subprocess
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner, Result

from stopline.cli import app as app_module
from stopline.cli import part as part_module
from stopline.cli.app import app, main
from stopline.core.contract import approval_matches, load_contract
from stopline.core.lifecycle import complete_part
from stopline.core.models import Status

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
    complete_part(path)

    result = _run("keep", "function:a.b", "--part", "billing", "--reason", "Helper")

    assert result.exit_code == 0
    contract = load_contract(path)
    assert contract.status is Status.COMPLETE
    assert contract.approved_extras[0].feature == "function:a.b"
    assert approval_matches(contract)


def test_keep_can_name_a_part_that_is_not_the_active_one(repo: Path, owner: None) -> None:
    """Spec section 13.1: --part wins over the active part."""
    first = _active(repo, "first")
    complete_part(first)
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
    complete_part(path)
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
