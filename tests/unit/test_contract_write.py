# Tests for creating and changing contract files (FR-CON-04, FR-CON-05, FR-CON-07).
import json
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stopline.core.contract import (
    SCHEMA_URL,
    ContractError,
    approval_hash,
    approval_matches,
    contract_warnings,
    keep_feature,
    load_contract,
    new_part,
)
from stopline.core.models import Status

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)

DRAFT = """\
# Drafted by the agent
schema  = 1
title   = "Billing"
summary = "Monthly subscriptions"  # keep this note
paths   = ["src/billing/**"]

# The first thing to build
[[items]]
id      = "invoice"
desc    = "Create an invoice"
anchors = ["route:POST /invoices"]
"""

SIGNED_BODY = """\
#:schema https://example.test/contract.json
# Top comment
schema  = 1
id      = "auth"
title   = "Authentication"
summary = "Login"  # note on the summary
paths   = ["src/auth/**"]
status  = "active"

# Comment on the item
[[items]]
id      = "login"
desc    = "Log in"
anchors = ["route:POST /auth/login"]

[baseline]
commit     = "a1b2c3d"
started_at = 2026-11-10T09:15:00Z
"""


def _write_signed(tmp_path: Path, body: str = SIGNED_BODY, *, crlf: bool = False) -> Path:
    """Write a contract with a correct signature, as `part approve` would leave it."""
    path = tmp_path / "auth.toml"
    path.write_text(body, encoding="utf-8")
    current = approval_hash(load_contract(path))
    signed = (
        f'{body}\n[approval]\nhash = "{current}"\n'
        'approved_by = "Devansh Shah"\napproved_at = 2026-11-10T09:12:00Z\n'
    )
    path.write_bytes((signed.replace("\n", "\r\n") if crlf else signed).encode("utf-8"))
    return path


def _keep(path: Path, feature: str = "function:auth.hash", reason: str = "Helper") -> None:
    keep_feature(path, feature, reason, approved_by="Owner", now=NOW)


def _files_in(folder: Path) -> list[str]:
    return sorted(entry.name for entry in folder.iterdir())


def test_template_is_a_valid_draft_that_warns(tmp_path: Path) -> None:
    """Spec section 8: `part new` creates a draft, and creates the folder if needed."""
    parts = tmp_path / ".stopline" / "parts"

    path = new_part("billing", parts)

    contract = load_contract(path)
    assert path == parts / "billing.toml"
    assert contract.id == "billing"
    assert contract.status is Status.DRAFT
    assert contract.approval is None
    assert path.read_text(encoding="utf-8").startswith(f"#:schema {SCHEMA_URL}\n")
    assert len(contract_warnings(contract)) == 1


@pytest.mark.parametrize("bad", ["../evil", "a/b", "Bad", "", "a\n", 'a"b', "a" * 49])
def test_bad_ids_write_nothing(tmp_path: Path, bad: str) -> None:
    """Spec section 9.2: an id cannot escape the parts folder or break the file."""
    parts = tmp_path / "root" / "parts"

    with pytest.raises(ContractError, match="not a valid part id"):
        new_part(bad, parts)

    assert _files_in(tmp_path) == []


def test_existing_part_is_not_overwritten(tmp_path: Path) -> None:
    """Spec section 8: `part new` never replaces work that exists."""
    path = new_part("billing", tmp_path)
    path.write_text("my edits", encoding="utf-8")

    with pytest.raises(ContractError, match="already exists"):
        new_part("billing", tmp_path)

    assert path.read_text(encoding="utf-8") == "my edits"


def test_toml_draft_keeps_its_text_and_gets_missing_fields(tmp_path: Path) -> None:
    """FR-CON-05: an agent's TOML is saved as a draft, comments included."""
    source = tmp_path / "billing.draft.toml"
    source.write_text(DRAFT, encoding="utf-8")

    path = new_part("billing", tmp_path / "parts", source)

    contract = load_contract(path)
    saved = path.read_text(encoding="utf-8")
    assert contract.id == "billing"
    assert contract.status is Status.DRAFT
    assert contract.approval is None
    assert saved.startswith(f"#:schema {SCHEMA_URL}\n")
    assert saved.endswith(DRAFT)


def test_toml_draft_with_its_own_fields_is_not_given_them_twice(tmp_path: Path) -> None:
    """FR-CON-05: an id, status and schema line already present are left as written."""
    first = '#:schema https://example.test/x.json\nid = "billing"\nstatus = "draft"\n'
    source = tmp_path / "draft.toml"
    source.write_text(first + DRAFT, encoding="utf-8")

    path = new_part("billing", tmp_path / "parts", source)

    assert path.read_text(encoding="utf-8") == first + DRAFT


@pytest.mark.parametrize(
    ("addition", "field"),
    [
        pytest.param('status = "approved"\n', "status", id="claims approved"),
        pytest.param('status = "active"\n', "status", id="claims active"),
        pytest.param('id = "other"\n', "id", id="wrong id"),
        pytest.param(
            'approval = { hash = "sha256:00", approved_by = "x" }\n', "approval", id="own signature"
        ),
        pytest.param('baseline = { commit = "a1b2c3d" }\n', "baseline", id="own baseline"),
    ],
)
def test_draft_cannot_claim_to_be_approved(tmp_path: Path, addition: str, field: str) -> None:
    """FR-CON-05: a draft is never approved, whatever the file says."""
    parts = tmp_path / "parts"
    source = tmp_path / "draft.toml"
    source.write_text(addition + DRAFT, encoding="utf-8")

    with pytest.raises(ContractError) as caught:
        new_part("billing", parts, source)

    assert caught.value.field == field
    assert caught.value.source == source
    assert caught.value.line == 1
    assert not parts.exists()


def test_invalid_draft_points_at_the_line_in_the_draft_file(tmp_path: Path) -> None:
    """FR-CON-01: filling in fields must not shift the line reported to the agent."""
    parts = tmp_path / "parts"
    source = tmp_path / "draft.toml"
    source.write_text(DRAFT.replace("route:POST /invoices", "widget:x"), encoding="utf-8")

    with pytest.raises(ContractError) as caught:
        new_part("billing", parts, source)

    assert caught.value.source == source
    assert caught.value.field == "items[0].anchors[0]"
    assert caught.value.line == DRAFT.split("\n").index('anchors = ["route:POST /invoices"]') + 1
    assert not parts.exists()


def test_broken_toml_draft_is_refused(tmp_path: Path) -> None:
    """FR-CON-05: a draft that is not TOML saves nothing."""
    source = tmp_path / "draft.toml"
    source.write_text("title =", encoding="utf-8")

    with pytest.raises(ContractError, match="Invalid TOML"):
        new_part("billing", tmp_path / "parts", source)


def test_missing_draft_file(tmp_path: Path) -> None:
    """FR-CON-05: the fix for a missing draft does not suggest creating a part."""
    with pytest.raises(ContractError) as caught:
        new_part("billing", tmp_path / "parts", tmp_path / "nope.toml")

    assert "does not exist" in caught.value.problem
    assert "path of the draft" in caught.value.fix


JSON_DRAFT: dict[str, object] = {
    "schema": 1,
    "title": "Billing",
    "summary": "Monthly",
    "paths": ["src/billing/**"],
    "allow_overlap": True,
    "items": [
        {
            "id": "invoice",
            "desc": "Create",
            "done_when": {"cmd": "pytest -q", "cwd": None, "timeout_s": 60},
        },
        {"id": "refund", "desc": "Refund", "done_when": ["pytest", "-k", "a  b"]},
    ],
    "approved_extras": [
        {"feature": "function:a.b", "reason": "Helper", "approved_at": "2026-11-10T11:40:00Z"}
    ],
}


def test_json_draft_becomes_a_toml_contract(tmp_path: Path) -> None:
    """FR-CON-05: JSON input is saved as TOML with the same meaning."""
    source = tmp_path / "draft.json"
    source.write_text(json.dumps(JSON_DRAFT), encoding="utf-8")

    path = new_part("billing", tmp_path / "parts", source)

    contract = load_contract(path)
    saved = path.read_text(encoding="utf-8")
    assert path.suffix == ".toml"
    assert saved.startswith(f"#:schema {SCHEMA_URL}\n")
    assert "approved_at = 2026-11-10T11:40:00Z" in saved  # a real TOML time, not text
    assert contract.status is Status.DRAFT
    assert contract.allow_overlap is True
    assert contract.summary == "Monthly"
    assert [item.id for item in contract.items] == ["invoice", "refund"]
    assert contract.items[1].done_when == ["pytest", "-k", "a  b"]
    assert contract.approved_extras[0].approved_at == datetime(2026, 11, 10, 11, 40, tzinfo=UTC)


def test_json_draft_cannot_claim_to_be_approved(tmp_path: Path) -> None:
    """FR-CON-05: the same refusal applies to JSON."""
    source = tmp_path / "draft.json"
    source.write_text(json.dumps({**JSON_DRAFT, "status": "approved"}), encoding="utf-8")

    with pytest.raises(ContractError) as caught:
        new_part("billing", tmp_path / "parts", source)

    assert caught.value.field == "status"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        pytest.param("{broken", "Invalid JSON", id="syntax"),
        pytest.param("[1, 2]", "must be a JSON object", id="not an object"),
        pytest.param('{"title": "\\ud800"}', "Invalid JSON", id="text that cannot be saved"),
        pytest.param('{"title": "x"}', "schema", id="missing field"),
    ],
)
def test_bad_json_draft_is_refused(tmp_path: Path, text: str, message: str) -> None:
    """FR-CON-05: bad JSON is a clear error, not a crash, and saves nothing."""
    parts = tmp_path / "parts"
    source = tmp_path / "draft.json"
    source.write_text(text, encoding="utf-8")

    with pytest.raises(ContractError, match=message):
        new_part("billing", parts, source)

    assert not parts.exists()


def test_keep_adds_the_extra_and_re_signs(tmp_path: Path) -> None:
    """FR-CON-04: one step adds the extra and leaves the contract correctly signed."""
    path = _write_signed(tmp_path)

    _keep(path)

    contract = load_contract(path)
    assert contract.approved_extras[0].feature == "function:auth.hash"
    assert contract.approved_extras[0].reason == "Helper"
    assert contract.approved_extras[0].approved_at == NOW
    assert approval_matches(contract)
    assert contract.approval is not None
    assert contract.approval.approved_by == "Owner"
    assert contract.approval.approved_at == NOW


def test_keep_changes_nothing_else(tmp_path: Path) -> None:
    """FR-CON-07: comments, status and baseline survive a write."""
    path = _write_signed(tmp_path)
    before = load_contract(path)

    _keep(path)

    saved = path.read_text(encoding="utf-8")
    after = load_contract(path)
    for comment in (
        "#:schema https://example.test/contract.json",
        "# Top comment",
        "# note on the summary",
        "# Comment on the item",
    ):
        assert comment in saved
    assert after.status is before.status
    assert after.baseline == before.baseline
    assert after.items == before.items


def test_keep_twice_appends_in_order(tmp_path: Path) -> None:
    """FR-CON-04: a second extra joins the first and the signature stays correct."""
    path = _write_signed(tmp_path)

    _keep(path)
    _keep(path, "class:auth.Token")

    contract = load_contract(path)
    assert [e.feature for e in contract.approved_extras] == [
        "function:auth.hash",
        "class:auth.Token",
    ]
    assert approval_matches(contract)


def test_keep_works_when_extras_are_written_as_a_list(tmp_path: Path) -> None:
    """FR-CON-04: TOML allows `approved_extras = [...]`, which is stored differently."""
    body = SIGNED_BODY.replace('status  = "active"', 'status  = "active"\napproved_extras = []')
    path = _write_signed(tmp_path, body)

    _keep(path)

    contract = load_contract(path)
    assert contract.approved_extras[0].feature == "function:auth.hash"
    assert approval_matches(contract)


def test_keep_works_on_a_windows_file(tmp_path: Path) -> None:
    """FR-CON-04: line endings changed by git do not stop a keep."""
    path = _write_signed(tmp_path, crlf=True)

    _keep(path)

    assert approval_matches(load_contract(path))


def test_keep_refuses_a_contract_edited_since_approval(tmp_path: Path) -> None:
    """FR-CON-03, FR-CON-04: re-signing here would approve an edit nobody reviewed."""
    path = _write_signed(tmp_path)
    tampered = path.read_text(encoding="utf-8").replace('"src/auth/**"', '"**"')
    path.write_text(tampered, encoding="utf-8")

    with pytest.raises(ContractError) as caught:
        _keep(path)

    assert "changed since it was approved" in caught.value.problem
    assert "stopline part approve auth" in caught.value.fix
    assert path.read_text(encoding="utf-8") == tampered


def test_keep_refuses_a_draft(tmp_path: Path) -> None:
    """FR-CON-04: a contract never approved has nothing to re-sign."""
    path = new_part("auth", tmp_path)

    with pytest.raises(ContractError, match="not approved"):
        _keep(path)


def test_keep_refuses_an_abandoned_part(tmp_path: Path) -> None:
    """Spec section 8: abandoned parts are kept for history."""
    path = _write_signed(tmp_path, SIGNED_BODY.replace('"active"', '"abandoned"'))

    with pytest.raises(ContractError, match="abandoned"):
        _keep(path)


def test_keep_refuses_a_repeated_feature(tmp_path: Path) -> None:
    """FR-CON-04: the same feature is not listed twice."""
    path = _write_signed(tmp_path)
    _keep(path)

    with pytest.raises(ContractError, match="already an approved extra"):
        _keep(path)


@pytest.mark.parametrize(
    ("feature", "reason"),
    [
        pytest.param("not-a-feature", "Helper", id="no kind"),
        pytest.param("widget:x", "Helper", id="unknown kind"),
        pytest.param("function:x\n", "Helper", id="line break"),
        pytest.param("function:x", "", id="empty reason"),
    ],
)
def test_keep_refuses_bad_input(tmp_path: Path, feature: str, reason: str) -> None:
    """Spec section 9.2: an approved extra needs a feature key and a reason."""
    path = _write_signed(tmp_path)
    before = path.read_bytes()

    with pytest.raises(ContractError, match="Cannot keep"):
        _keep(path, feature, reason)

    assert path.read_bytes() == before


def test_failed_save_keeps_the_old_file_and_no_leftovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-CON-07: if the final swap fails, the contract is untouched."""
    path = _write_signed(tmp_path)
    before = path.read_bytes()

    def fail(self: Path, target: Path) -> Path:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "replace", fail)

    with pytest.raises(ContractError, match="No space left"):
        _keep(path)

    assert path.read_bytes() == before
    assert _files_in(tmp_path) == ["auth.toml"]


def test_folder_that_cannot_be_created_is_a_contract_error(tmp_path: Path) -> None:
    """FR-CON-07: a file where the folder should be is a clear error, not a crash."""
    blocker = tmp_path / "parts"
    blocker.write_text("", encoding="utf-8")

    with pytest.raises(ContractError, match="cannot be saved"):
        new_part("billing", blocker)


@pytest.mark.skipif(os.name == "nt", reason="Windows does not use these permission bits")
def test_saved_files_are_readable_and_keep_their_permissions(tmp_path: Path) -> None:
    """FR-CON-07: the private temp file's permissions never reach the contract."""
    new_file = new_part("billing", tmp_path)
    existing = _write_signed(tmp_path)
    existing.chmod(0o640)

    _keep(existing)

    assert stat.S_IMODE(new_file.stat().st_mode) == 0o644
    assert stat.S_IMODE(existing.stat().st_mode) == 0o640


def test_reading_contracts_does_not_load_the_toml_writer() -> None:
    """Spec section 18.3: tomlkit is loaded only when writing, never by hooks."""
    code = "import sys, stopline.core.contract;sys.exit('tomlkit' in sys.modules)"

    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0  # noqa: S603
