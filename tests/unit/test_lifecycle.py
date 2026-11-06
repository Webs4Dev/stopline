# Tests for moving parts between states and the rules for each move (spec section 8).
import io
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stopline.core.contract import ContractError, approval_matches, load_contract
from stopline.core.lifecycle import (
    OwnerOnlyError,
    abandon_part,
    approve_part,
    checked_part,
    complete_part,
    globs_overlap,
    list_parts,
    pause_part,
    reopen_part,
    require_owner,
    start_part,
)
from stopline.core.models import Status

APPROVED_AT = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
STARTED_AT = datetime(2026, 11, 6, 10, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)

BODY = """\
#:schema https://example.test/contract.json
# A comment to keep
schema  = 1
id      = "{id}"
title   = "Title"
summary = "Summary"
paths   = ["{glob}"]
status  = "draft"  # a note to keep
{extra}
# About the item
[[items]]
id      = "one"
desc    = "One"
anchors = ["route:GET /one"]
"""


class FakeGit:
    """Stands in for git, so these tests need no repository."""

    def __init__(self, dirty: list[str] | None = None, commit: str = "a1b2c3d") -> None:
        self.dirty = dirty or []
        self.commit = commit
        self.asked_for: Sequence[str] = []

    def head(self) -> str:
        return self.commit

    def dirty_paths(self, globs: Sequence[str]) -> list[str]:
        self.asked_for = globs
        return self.dirty


def _draft(parts: Path, part_id: str = "auth", glob: str | None = None, extra: str = "") -> Path:
    parts.mkdir(exist_ok=True)
    path = parts / f"{part_id}.toml"
    text = BODY.format(id=part_id, glob=glob or f"src/{part_id}/**", extra=extra)
    path.write_text(text, encoding="utf-8")
    return path


def _approved(parts: Path, part_id: str = "auth", **kwargs: str) -> Path:
    path = _draft(parts, part_id, **kwargs)
    approve_part(path, "Owner", APPROVED_AT)
    return path


def _active(parts: Path, part_id: str = "auth", **kwargs: str) -> Path:
    path = _approved(parts, part_id, **kwargs)
    start_part(path, FakeGit(), STARTED_AT)
    return path


def _complete(parts: Path, part_id: str = "auth") -> Path:
    path = _active(parts, part_id)
    complete_part(path)
    return path


def _status(path: Path) -> Status:
    return load_contract(path).status


def _edit(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('"Summary"', '"Edited"'), encoding="utf-8")


def test_approve_signs_a_draft(tmp_path: Path) -> None:
    """Spec section 8: approval records the hash, the approver and the time."""
    path = _approved(tmp_path)

    contract = load_contract(path)
    assert contract.status is Status.APPROVED
    assert approval_matches(contract)
    assert contract.approval is not None
    assert contract.approval.approved_by == "Owner"
    assert contract.approval.approved_at == APPROVED_AT


def test_writes_keep_comments(tmp_path: Path) -> None:
    """FR-CON-07: moving through the states leaves the owner's comments in place."""
    path = _complete(tmp_path)

    saved = path.read_text(encoding="utf-8")
    assert "#:schema https://example.test/contract.json" in saved
    assert "# A comment to keep" in saved
    assert "# a note to keep" in saved
    assert "# About the item" in saved


@pytest.mark.parametrize("make", [_approved, _active, _complete])
def test_approve_re_signs_an_edit_and_keeps_status_and_baseline(
    tmp_path: Path, make: Callable[[Path], Path]
) -> None:
    """FR-LC-06: approved, active and complete parts can be re-signed after an edit."""
    path = make(tmp_path)
    before = load_contract(path)
    _edit(path)
    assert not approval_matches(load_contract(path))

    approve_part(path, "Owner", LATER)

    after = load_contract(path)
    assert approval_matches(after)
    assert after.status is before.status
    assert after.baseline == before.baseline


def test_approve_refuses_an_abandoned_part(tmp_path: Path) -> None:
    """Spec section 8: abandoned is the end of the road."""
    path = _approved(tmp_path)
    abandon_part(path)

    with pytest.raises(ContractError, match="abandoned"):
        approve_part(path, "Owner", LATER)


def test_approve_refuses_shared_paths(tmp_path: Path) -> None:
    """Spec section 9.2: two parts may not share paths by default."""
    _draft(tmp_path, "other", glob="src/**")
    path = _draft(tmp_path, "auth", glob="src/auth/**")

    with pytest.raises(ContractError) as caught:
        approve_part(path, "Owner", APPROVED_AT)

    assert "overlaps `src/**` in part `other`" in caught.value.problem
    assert caught.value.field == "paths"
    assert _status(path) is Status.DRAFT


def test_approve_allows_shared_paths_only_if_both_parts_agree(tmp_path: Path) -> None:
    """Spec section 9.2: allow_overlap must be true on both."""
    overlap = "allow_overlap = true\n"
    other = _draft(tmp_path, "other", glob="src/**")
    path = _draft(tmp_path, "auth", glob="src/auth/**", extra=overlap)

    with pytest.raises(ContractError, match="overlaps"):
        approve_part(path, "Owner", APPROVED_AT)

    _draft(tmp_path, "other", glob="src/**", extra=overlap)
    approve_part(path, "Owner", APPROVED_AT)
    assert _status(path) is Status.APPROVED
    assert _status(other) is Status.DRAFT


def test_abandoned_parts_do_not_count_for_shared_paths(tmp_path: Path) -> None:
    """Spec section 9.2: only non-abandoned parts can clash."""
    abandon_part(_draft(tmp_path, "other", glob="src/**"))
    path = _draft(tmp_path, "auth", glob="src/auth/**")

    approve_part(path, "Owner", APPROVED_AT)

    assert _status(path) is Status.APPROVED


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("src/auth/**", "src/auth/**", True),
        ("./src/auth/**", "src/auth/**", True),
        ("src/**", "src/auth/**", True),
        ("src/auth/**", "src/**", True),
        ("**", "anything/at/all.py", True),
        ("src/**", "src/a.py", True),
        ("src/auth", "src/auth/a.py", True),
        ("src/auth/", "src/auth/a.py", True),
        ("src/*.py", "src/**", True),
        ("src/auth/**", "src/billing/**", False),
        ("src/auth/**", "src/authors/**", False),
        ("src/*.py", "src/a.py", False),  # known limit: see globs_overlap
    ],
)
def test_globs_overlap(first: str, second: str, expected: bool) -> None:
    """Spec section 9.2: identical and nested globs are caught, in either order."""
    assert globs_overlap(first, second) is expected
    assert globs_overlap(second, first) is expected


def test_start_makes_a_part_active_and_records_the_baseline(tmp_path: Path) -> None:
    """Spec section 8: baseline = HEAD at the first start."""
    git = FakeGit()
    path = _approved(tmp_path)

    start_part(path, git, STARTED_AT)

    contract = load_contract(path)
    assert contract.status is Status.ACTIVE
    assert contract.baseline is not None
    assert contract.baseline.commit == "a1b2c3d"
    assert contract.baseline.started_at == STARTED_AT
    assert git.asked_for == ["src/auth/**"]
    assert approval_matches(contract)


def test_start_after_pause_keeps_the_original_baseline(tmp_path: Path) -> None:
    """Spec section 8: resuming cannot move the baseline and hide earlier work."""
    path = _active(tmp_path)
    before = load_contract(path).baseline

    assert pause_part(tmp_path) == "auth"
    assert _status(path) is Status.APPROVED
    assert load_contract(path).baseline == before

    start_part(path, FakeGit(commit="fffffff"), LATER)

    assert _status(path) is Status.ACTIVE
    assert load_contract(path).baseline == before


@pytest.mark.parametrize(
    ("make", "hint"),
    [
        pytest.param(_draft, "part approve", id="draft"),
        pytest.param(_active, "nothing to do", id="already active"),
        pytest.param(_complete, "part reopen", id="complete"),
    ],
)
def test_start_refuses_anything_but_approved(
    tmp_path: Path, make: Callable[[Path], Path], hint: str
) -> None:
    """Spec section 8: only approved moves to active."""

    with pytest.raises(ContractError) as caught:
        start_part(make(tmp_path), FakeGit(), LATER)

    assert hint in caught.value.fix


def test_start_refuses_an_abandoned_part(tmp_path: Path) -> None:
    """Spec section 8: abandoned is the end of the road."""
    path = _approved(tmp_path)
    abandon_part(path)

    with pytest.raises(ContractError, match="abandoned"):
        start_part(path, FakeGit(), LATER)


def test_start_refuses_a_contract_edited_since_approval(tmp_path: Path) -> None:
    """FR-CON-03: an edited contract counts as unapproved."""
    path = _approved(tmp_path)
    _edit(path)

    with pytest.raises(ContractError, match="changed since"):
        start_part(path, FakeGit(), LATER)

    assert _status(path) is Status.APPROVED


def test_only_one_part_can_be_active(tmp_path: Path) -> None:
    """FR-LC-02: at most one active part per worktree."""
    _active(tmp_path, "auth")
    other = _approved(tmp_path, "billing")

    with pytest.raises(ContractError, match="`auth` is already active"):
        start_part(other, FakeGit(), LATER)

    assert _status(other) is Status.APPROVED


def test_start_refuses_uncommitted_changes_unless_allowed(tmp_path: Path) -> None:
    """Spec section 8: uncommitted work would hide inside the baseline."""
    path = _approved(tmp_path)
    git = FakeGit(dirty=["src/auth/a.py", "src/auth/b.py"])

    with pytest.raises(ContractError, match=r"src/auth/a\.py, src/auth/b\.py") as caught:
        start_part(path, git, LATER)

    assert "--allow-dirty" in caught.value.fix
    assert _status(path) is Status.APPROVED

    start_part(path, git, LATER, allow_dirty=True)
    assert _status(path) is Status.ACTIVE


def test_pause_with_nothing_active(tmp_path: Path) -> None:
    """Spec section 8: there is nothing to pause."""
    _approved(tmp_path)

    with pytest.raises(ContractError, match="No part is active"):
        pause_part(tmp_path)


def test_complete_is_idempotent(tmp_path: Path) -> None:
    """FR-LC-04: completing twice changes nothing, not even the file."""
    path = _complete(tmp_path)
    before = path.read_bytes()

    complete_part(path)

    assert _status(path) is Status.COMPLETE
    assert path.read_bytes() == before


def test_complete_refuses_a_part_that_is_not_active(tmp_path: Path) -> None:
    """Spec section 8: only active moves to complete."""
    with pytest.raises(ContractError, match="cannot complete"):
        complete_part(_approved(tmp_path))


def test_complete_refuses_a_contract_edited_since_approval(tmp_path: Path) -> None:
    """FR-CON-03: an edited contract cannot complete."""
    path = _active(tmp_path)
    _edit(path)

    with pytest.raises(ContractError, match="changed since"):
        complete_part(path)

    assert _status(path) is Status.ACTIVE


def test_reopen_moves_complete_back_to_active(tmp_path: Path) -> None:
    """Spec section 8: reopening keeps the baseline."""
    path = _complete(tmp_path)
    before = load_contract(path).baseline

    reopen_part(path)

    assert _status(path) is Status.ACTIVE
    assert load_contract(path).baseline == before


def test_reopen_refuses_a_part_that_is_not_complete(tmp_path: Path) -> None:
    """Spec section 8: only complete moves back to active."""
    with pytest.raises(ContractError, match="only a complete part"):
        reopen_part(_active(tmp_path))


def test_reopen_respects_the_one_active_part_rule(tmp_path: Path) -> None:
    """FR-LC-02: reopening cannot create a second active part."""
    path = _complete(tmp_path, "auth")
    _active(tmp_path, "billing")

    with pytest.raises(ContractError, match="`billing` is already active"):
        reopen_part(path)

    assert _status(path) is Status.COMPLETE


@pytest.mark.parametrize("make", [_draft, _approved, _active])
def test_abandon_works_from_any_state_but_complete(
    tmp_path: Path, make: Callable[[Path], Path]
) -> None:
    """Spec section 8: the part stays on disk for history."""
    path = make(tmp_path)

    abandon_part(path)

    assert _status(path) is Status.ABANDONED


def test_abandon_refuses_a_complete_part(tmp_path: Path) -> None:
    """Spec section 8: "any but complete"."""
    path = _complete(tmp_path)

    with pytest.raises(ContractError, match="cannot be abandoned"):
        abandon_part(path)

    assert _status(path) is Status.COMPLETE


def test_abandon_twice_is_refused(tmp_path: Path) -> None:
    """Spec section 8: an abandoned part has nowhere left to go."""
    path = _draft(tmp_path)
    abandon_part(path)

    with pytest.raises(ContractError, match="cannot be abandoned"):
        abandon_part(path)


def test_checked_part_is_the_active_one(tmp_path: Path) -> None:
    """Spec section 5: the active part is the checked part."""
    _complete(tmp_path, "older")
    _active(tmp_path, "auth")

    checked = checked_part(tmp_path, last_completed="older")

    assert checked is not None
    assert checked.id == "auth"


def test_completed_part_stays_checked_until_another_starts(tmp_path: Path) -> None:
    """FR-LC-05: its paths stay protected until another part starts."""
    _complete(tmp_path, "auth")
    other = _approved(tmp_path, "billing")

    checked = checked_part(tmp_path, last_completed="auth")
    assert checked is not None
    assert checked.id == "auth"

    start_part(other, FakeGit(), LATER)
    checked = checked_part(tmp_path, last_completed="auth")
    assert checked is not None
    assert checked.id == "billing"


@pytest.mark.parametrize("last_completed", [None, "nope"])
def test_no_checked_part(tmp_path: Path, last_completed: str | None) -> None:
    """Spec section 5: with nothing active or known to be completed, nothing is checked."""
    _complete(tmp_path, "auth")

    assert checked_part(tmp_path, last_completed) is None


def test_list_parts_handles_a_missing_folder(tmp_path: Path) -> None:
    """Spec section 8: a repository with no parts yet is normal."""
    assert list_parts(tmp_path / "none") == []


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_owner_guard_allows_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-LC-03: a person at a terminal may run owner-only commands."""
    monkeypatch.delenv("STOPLINE_ALLOW_NONINTERACTIVE", raising=False)
    monkeypatch.setattr("sys.stdin", _Terminal())

    require_owner("part approve")


def test_owner_guard_refuses_without_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-LC-03: agent shells have no terminal."""
    monkeypatch.delenv("STOPLINE_ALLOW_NONINTERACTIVE", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO())

    with pytest.raises(OwnerOnlyError, match="stopline part approve"):
        require_owner("part approve")


@pytest.mark.parametrize(("value", "allowed"), [("1", True), ("0", False), ("", False)])
def test_owner_guard_environment_override(
    monkeypatch: pytest.MonkeyPatch, value: str, allowed: bool
) -> None:
    """FR-LC-03: only the exact documented value opens the guard."""
    monkeypatch.setenv("STOPLINE_ALLOW_NONINTERACTIVE", value)
    monkeypatch.setattr("sys.stdin", io.StringIO())

    if allowed:
        require_owner("part approve")
    else:
        with pytest.raises(OwnerOnlyError):
            require_owner("part approve")
