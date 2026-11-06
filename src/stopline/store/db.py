# Opens .stopline/state.db: creates it, upgrades it, and replaces it if it is damaged
# (spec section 15.2). It holds only local data, so it never goes into git.
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

_GITIGNORE = "state.db*\nlogs/\nruns/\n"
_SECONDS_TO_WAIT_IF_BUSY = 2.0
_SECONDS_BETWEEN_TRIES = 0.02

# SQLite's own codes for "this file is damaged" and "this is not a database".
_DAMAGED_CODES = (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)
_BUSY_CODES = (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)


class StateError(Exception):
    """state.db cannot be used right now, and rebuilding it would not help."""


class _Incompatible(Exception):
    """state.db was written by a newer Stopline, or its version cannot be read."""


@dataclass(frozen=True)
class State:
    """An open database. `set_aside` is where a damaged one went, if this one replaced it.

    A replacement holds no trust records, so every part is untrusted until the owner runs
    `stopline trust` (FR-CON-08). Callers should say so when `set_aside` is not None.
    """

    connection: sqlite3.Connection
    set_aside: Path | None = None


def open_state(stopline_dir: Path) -> State:
    """Open the database in the .stopline folder, creating or replacing it as needed."""
    path = stopline_dir / "state.db"
    try:
        _keep_out_of_git(stopline_dir)
        try:
            return State(_connect(path))
        except _Incompatible:
            return _replace(path)
        except sqlite3.DatabaseError as err:
            if not _is_damage(err):
                raise
            return _replace(path)
    except (sqlite3.Error, OSError, _Incompatible) as err:
        raise StateError(
            f"{path}: The database cannot be used: {err}.\n"
            "  Fix: Check that you can write to this folder, and that no other program "
            "is holding the file. Then try again."
        ) from None


def _keep_out_of_git(stopline_dir: Path) -> None:
    """Spec section 15.1: state.db is ignored by git. An existing file is left as it is."""
    stopline_dir.mkdir(parents=True, exist_ok=True)
    ignore_file = stopline_dir / ".gitignore"
    if not ignore_file.exists():
        ignore_file.write_text(_GITIGNORE, encoding="utf-8")


def _is_damage(err: sqlite3.DatabaseError) -> bool:
    """Only real damage counts. "Busy" must never replace the file, or trust records are lost."""
    return (err.sqlite_errorcode & 0xFF) in _DAMAGED_CODES


def _connect(path: Path) -> sqlite3.Connection:
    """Open the database, waiting while it is busy.

    SQLite's own wait does not cover everything: switching on WAL mode fails at once if another
    program is doing the same, so this tries again until the same time limit runs out.
    """
    give_up_at = time.monotonic() + _SECONDS_TO_WAIT_IF_BUSY
    while True:
        try:
            return _connect_once(path)
        except sqlite3.OperationalError as err:
            busy = (err.sqlite_errorcode & 0xFF) in _BUSY_CODES
            if not busy or time.monotonic() >= give_up_at:
                raise
            time.sleep(_SECONDS_BETWEEN_TRIES)


def _connect_once(path: Path) -> sqlite3.Connection:
    # isolation_level=None means Stopline starts and ends every transaction itself.
    connection = sqlite3.connect(path, timeout=_SECONDS_TO_WAIT_IF_BUSY, isolation_level=None)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        _upgrade(connection)
    except BaseException:
        connection.close()
        raise
    return connection


def _replace(path: Path) -> State:
    """Spec section 15.2: rename the unusable file, keeping it for inspection, and start again."""
    set_aside = path.with_name(f"{path.name}.broken-{time.time_ns()}")
    for suffix in ("", "-wal", "-shm"):
        part = path.with_name(f"{path.name}{suffix}")
        if part.exists():
            part.replace(f"{set_aside}{suffix}")
    return State(_connect(path), set_aside)


def _upgrade(connection: sqlite3.Connection) -> None:
    """Spec section 15.2: apply the numbered upgrades this database has not had, oldest first."""
    migrations = _migrations()
    newest = max(migrations)
    if _version(connection, newest) == newest:
        return
    # Taking the write lock first means two processes starting together cannot both upgrade.
    connection.execute("BEGIN IMMEDIATE")
    try:
        current = _version(connection, newest)
        for number in sorted(migrations):
            if number > current:
                for statement in migrations[number].split(";"):
                    connection.execute(statement)
        if current < newest:
            connection.execute(
                "INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(newest),)
            )
        if current == 0:
            connection.execute(
                "INSERT INTO meta VALUES ('created_at', ?)", (datetime.now(UTC).isoformat(),)
            )
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:  # SQLite ends the transaction itself after some errors
            connection.execute("ROLLBACK")
        raise


def _version(connection: sqlite3.Connection, newest: int) -> int:
    """Return the database's version, 0 if it is new. Refuse one this Stopline cannot read."""
    has_meta = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
    ).fetchone()
    if not has_meta:
        return 0
    row = connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        return 0
    try:
        version = int(row[0])
    except (TypeError, ValueError):
        raise _Incompatible(f"unreadable version {row[0]!r}") from None
    if version > newest:
        raise _Incompatible(f"version {version} is newer than this Stopline")
    return version


def _migrations() -> dict[int, str]:
    """Return the upgrade scripts by number. Files are named like 001_initial.sql."""
    folder = resources.files("stopline.store").joinpath("migrations")
    return {
        int(script.name.split("_")[0]): script.read_text(encoding="utf-8")
        for script in folder.iterdir()
        if script.name.endswith(".sql")
    }
