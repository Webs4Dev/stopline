# Local trust records (FR-CON-08, FR-CON-09) and the part index (spec section 15.2).
#
# A contract file travels through git, so anyone can write one. The trust record lives only in
# this clone's state.db and says "the owner accepted this contract, in this state". Only
# `part approve` and `stopline trust` may create or replace one. Every other command that
# changes a contract must find a matching record first, and moves the record along with it.
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from stopline.core.contract import ContractError, load_contract
from stopline.core.lifecycle import complete_part
from stopline.core.models import Contract


@dataclass(frozen=True)
class TrustRecord:
    """What the owner accepted: the approval hash, status and baseline commit, and who, when."""

    part_id: str
    approval_hash: str | None
    status: str
    baseline: str | None
    trusted_by: str
    trusted_at: str


def read_record(connection: sqlite3.Connection, part_id: str) -> TrustRecord | None:
    """Return the part's trust record, or None if this clone has none."""
    row = connection.execute(
        "SELECT part_id, approval_hash, status, baseline, trusted_by, trusted_at"
        " FROM trust WHERE part_id = ?",
        (part_id,),
    ).fetchone()
    return None if row is None else TrustRecord(*row)


def differences(record: TrustRecord | None, contract: Contract) -> list[str]:
    """Return what differs between the contract file and its record (FR-CON-08).

    An empty list means trusted. A missing record is untrusted too.
    """
    if record is None:
        return ["no trust record in this copy of the project"]
    approval_hash, status, baseline = _state(contract)
    found = []
    if record.approval_hash != approval_hash:
        found.append("approval")
    if record.status != status:
        found.append(f"status (trusted: {record.status}, file: {status})")
    if record.baseline != baseline:
        found.append("baseline")
    return found


def is_trusted(connection: sqlite3.Connection, contract: Contract) -> bool:
    """FR-CON-08: whether the contract matches its local trust record."""
    return not differences(read_record(connection, contract.id), contract)


@contextmanager
def owner_change(
    connection: sqlite3.Connection, path: Path, trusted_by: str, now: datetime
) -> Iterator[None]:
    """Wrap a change only the owner may make, then record the result as trusted (FR-CON-09).

    For `part approve` and `stopline trust` only. Nothing is checked first.
    """
    with _transaction(connection):
        yield
        contract = load_contract(path)
        connection.execute(
            "INSERT OR REPLACE INTO trust VALUES (?, ?, ?, ?, ?, ?)",
            (contract.id, *_state(contract), trusted_by, _stamp(now)),
        )
        _index(connection, contract, now)


@contextmanager
def trusted_change(
    connection: sqlite3.Connection, path: Path, now: datetime, *, completed_tree: str | None = None
) -> Iterator[None]:
    """Wrap any other change to a contract (FR-CON-09).

    Refuses unless the file matches its trust record, so running a command an agent is
    allowed to run can never make a contract trusted. After the change, the record follows
    the file; who trusted it and when stay the same.
    """
    with _transaction(connection):
        before = load_contract(path)
        found = differences(read_record(connection, before.id), before)
        if found:
            problem = (
                f"Part `{before.id}` is not trusted in this copy of the project: "
                f"{'; '.join(found)}. It may have come from someone else, or been "
                "changed outside Stopline."
            )
            fix = (
                f"Review it, then run `stopline part approve {before.id}` or "
                f"`stopline trust {before.id}` yourself in a terminal."
            )
            raise ContractError(path, problem, fix, field="approval")
        yield
        after = load_contract(path)
        connection.execute(
            "UPDATE trust SET approval_hash = ?, status = ?, baseline = ? WHERE part_id = ?",
            (*_state(after), after.id),
        )
        _index(connection, after, now, completed_tree)


def complete(connection: sqlite3.Connection, path: Path, tree_hash: str, now: datetime) -> None:
    """Complete a trusted part, recording the tree hash and time (spec section 8, FR-LC-04)."""
    with trusted_change(connection, path, now, completed_tree=tree_hash):
        complete_part(path)


def last_completed(connection: sqlite3.Connection) -> str | None:
    """Return the complete part changed most recently, which checks fall back to (FR-LC-05).

    This answers open question 3. Keeping a feature in a complete part counts as a change.
    """
    row = connection.execute(
        "SELECT part_id FROM part_index WHERE status = 'complete'"
        " ORDER BY updated_at DESC, part_id LIMIT 1"
    ).fetchone()
    return None if row is None else str(row[0])


def _state(contract: Contract) -> tuple[str | None, str, str | None]:
    approval_hash = contract.approval.hash if contract.approval else None
    baseline = contract.baseline.commit if contract.baseline else None
    return approval_hash, contract.status.value, baseline


def _index(
    connection: sqlite3.Connection,
    contract: Contract,
    now: datetime,
    completed_tree: str | None = None,
) -> None:
    """Keep part_index in step with the contract. completed_tree changes only on completion."""
    approval_hash, status, baseline = _state(contract)
    connection.execute(
        "INSERT INTO part_index VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (part_id) DO UPDATE SET"
        " status = excluded.status, baseline = excluded.baseline,"
        " approval_hash = excluded.approval_hash,"
        " completed_tree = coalesce(excluded.completed_tree, completed_tree),"
        " updated_at = excluded.updated_at",
        (contract.id, status, baseline, approval_hash, completed_tree, _stamp(now)),
    )


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """Hold the database's write lock across check, file write and record update.

    Two Stopline commands can then never interleave. If the process stops after the file
    is written but before the record is, the part is merely untrusted: the safe side.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")
