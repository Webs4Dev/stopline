# Tests for local trust records and the part index (FR-CON-08, FR-CON-09, FR-LC-01, spec 15.2).
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stopline.core import trust
from stopline.core.contract import ContractError, keep_feature, load_contract
from stopline.core.lifecycle import (
    approve_part,
    complete_part,
    pause_part,
    reopen_part,
    start_part,
)
from stopline.core.models import Status
from stopline.store.db import open_state

NOW = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=1)

BODY = """\
schema  = 1
id      = "{id}"
title   = "Title"
summary = "Summary"
paths   = ["src/{id}/**"]
status  = "draft"

[[items]]
id      = "one"
desc    = "One"
anchors = ["route:GET /one"]
"""


class FakeGit:
    def head(self) -> str:
        return "a1b2c3d"

    def dirty_paths(self, globs: object) -> list[str]:
        return []


@pytest.fixture
def stopline_dir(tmp_path: Path) -> Path:
    (tmp_path / ".stopline" / "parts").mkdir(parents=True)
    return tmp_path / ".stopline"


@pytest.fixture
def db(stopline_dir: Path) -> Iterator[sqlite3.Connection]:
    connection = open_state(stopline_dir).connection
    yield connection
    connection.close()


def _draft(stopline_dir: Path, part_id: str = "auth") -> Path:
    path = stopline_dir / "parts" / f"{part_id}.toml"
    path.write_text(BODY.format(id=part_id), encoding="utf-8")
    return path


def _approve(db: sqlite3.Connection, path: Path) -> None:
    with trust.owner_change(db, path, "Owner", NOW):
        approve_part(path, "Owner", NOW)


def _start(db: sqlite3.Connection, path: Path) -> None:
    with trust.trusted_change(db, path, NOW):
        start_part(path, FakeGit(), NOW)


def _active(db: sqlite3.Connection, stopline_dir: Path, part_id: str = "auth") -> Path:
    path = _draft(stopline_dir, part_id)
    _approve(db, path)
    _start(db, path)
    return path


def _replace_text(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_approve_creates_a_matching_record(db: sqlite3.Connection, stopline_dir: Path) -> None:
    """FR-CON-09: `part approve` creates the record, with who and when."""
    path = _draft(stopline_dir)
    _approve(db, path)

    record = trust.read_record(db, "auth")
    contract = load_contract(path)
    assert record is not None
    assert contract.approval is not None
    assert (record.approval_hash, record.status, record.baseline) == (
        contract.approval.hash,
        "approved",
        None,
    )
    assert (record.trusted_by, record.trusted_at) == ("Owner", "2026-11-07T09:00:00+00:00")
    assert trust.is_trusted(db, contract)


def test_record_follows_each_trusted_change(db: sqlite3.Connection, stopline_dir: Path) -> None:
    """FR-CON-09 and FR-LC-01: start, keep, pause and reopen move the record with the file."""
    path = _active(db, stopline_dir)
    with trust.trusted_change(db, path, LATER):
        keep_feature(path, "function:a.b", "Helper", "Agent", LATER)
    assert trust.is_trusted(db, load_contract(path))

    with trust.trusted_change(db, path, LATER):
        pause_part(stopline_dir / "parts")
    record = trust.read_record(db, "auth")
    assert record is not None
    assert (record.status, record.baseline) == ("approved", "a1b2c3d")
    # Who trusted it, and when, do not change: the owner did not run these commands.
    assert (record.trusted_by, record.trusted_at) == ("Owner", "2026-11-07T09:00:00+00:00")


def test_no_record_is_untrusted_and_refused(db: sqlite3.Connection, stopline_dir: Path) -> None:
    """FR-CON-08: a fresh clone or rebuilt database has no record, so the part is untrusted."""
    path = _draft(stopline_dir)
    approve_part(path, "Someone else", NOW)  # signed in another clone, arrived through git
    before = path.read_bytes()

    assert not trust.is_trusted(db, load_contract(path))
    with pytest.raises(ContractError, match="no trust record"), trust.trusted_change(db, path, NOW):
        start_part(path, FakeGit(), NOW)

    assert path.read_bytes() == before
    assert trust.read_record(db, "auth") is None


@pytest.mark.parametrize(
    ("old", "new", "field"),
    [
        ('status  = "active"', 'status  = "complete"', "status"),
        ('commit = "a1b2c3d"', 'commit = "fffffff"', "baseline"),
    ],
)
def test_hand_edits_to_status_or_baseline_are_refused(
    db: sqlite3.Connection, stopline_dir: Path, old: str, new: str, field: str
) -> None:
    """FR-CON-08: status and baseline are outside the approval hash, so the record guards them."""
    path = _active(db, stopline_dir)
    _replace_text(path, old, new)

    assert not trust.is_trusted(db, load_contract(path))
    with pytest.raises(ContractError, match=field), trust.trusted_change(db, path, LATER):
        keep_feature(path, "function:a.b", "Helper", "Agent", LATER)


def test_contract_re_signed_elsewhere_is_refused(
    db: sqlite3.Connection, stopline_dir: Path
) -> None:
    """FR-CON-09: a valid signature made in another clone is not this owner's approval."""
    path = _active(db, stopline_dir)
    _replace_text(path, '"Summary"', '"Do far more"')
    approve_part(path, "Someone else", LATER)  # a valid signature, but not through this clone

    with pytest.raises(ContractError, match="approval"), trust.trusted_change(db, path, LATER):
        keep_feature(path, "function:a.b", "Helper", "Agent", LATER)


def test_owner_approval_restores_trust(db: sqlite3.Connection, stopline_dir: Path) -> None:
    """FR-CON-09: after reviewing, the owner's `part approve` makes the part trusted again."""
    path = _active(db, stopline_dir)
    _replace_text(path, 'status  = "active"', 'status  = "complete"')

    _approve(db, path)

    assert trust.is_trusted(db, load_contract(path))


def test_a_failed_change_leaves_the_record_alone(
    db: sqlite3.Connection, stopline_dir: Path
) -> None:
    """FR-CON-09: the record moves only when the change itself succeeds."""
    path = _active(db, stopline_dir)
    before = trust.read_record(db, "auth")

    with pytest.raises(ContractError), trust.trusted_change(db, path, LATER):
        reopen_part(path)  # an active part cannot be reopened

    assert trust.read_record(db, "auth") == before
    assert not db.in_transaction


def test_complete_records_the_tree_and_is_the_fallback_part(
    db: sqlite3.Connection, stopline_dir: Path
) -> None:
    """Spec section 8 and FR-LC-05: completion records the tree hash; checks fall back to it."""
    first = _active(db, stopline_dir, "first")
    trust.complete(db, first, "tree-1", NOW)
    second = _active(db, stopline_dir, "second")
    trust.complete(db, second, "tree-2", LATER)

    assert load_contract(second).status is Status.COMPLETE
    rows = dict(db.execute("SELECT part_id, completed_tree FROM part_index").fetchall())
    assert rows == {"first": "tree-1", "second": "tree-2"}
    assert trust.last_completed(db) == "second"

    with trust.trusted_change(db, second, LATER + timedelta(hours=1)):
        reopen_part(second)
    assert trust.last_completed(db) == "first"


def test_completing_an_untrusted_part_is_refused(
    db: sqlite3.Connection, stopline_dir: Path
) -> None:
    """FR-CON-08: an untrusted part cannot complete."""
    path = _active(db, stopline_dir)
    db.execute("DELETE FROM trust")

    with pytest.raises(ContractError, match="no trust record"):
        trust.complete(db, path, "tree", LATER)
    assert load_contract(path).status is Status.ACTIVE


def test_file_only_completion_counts_as_an_outside_change(
    db: sqlite3.Connection, stopline_dir: Path
) -> None:
    """FR-LC-01: the file and the record are compared; a change made around Stopline shows."""
    path = _active(db, stopline_dir)
    complete_part(path)

    assert not trust.is_trusted(db, load_contract(path))
