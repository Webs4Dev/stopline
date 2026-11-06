# Tests for opening, upgrading and replacing state.db (spec section 15.2, FR-CON-08).
import sqlite3
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from stopline.store import db
from stopline.store.db import State, StateError, open_state

TABLES = {
    "meta",
    "feature_cache",
    "part_index",
    "check_ledger",
    "item_runs",
    "block_history",
    "trust",
}


def _close(state: State) -> None:
    state.connection.close()


def _note_trust(folder: Path) -> None:
    """Leave a row in a closed database, so tests can see whether it survives."""
    state = open_state(folder)
    state.connection.execute(
        "INSERT INTO trust VALUES ('auth', 'sha256:00', 'active', 'a1b2c3d', 'Owner', 'now')"
    )
    _close(state)


def _trusted_parts(state: State) -> list[str]:
    return [row[0] for row in state.connection.execute("SELECT part_id FROM trust")]


def test_new_database_has_every_table(tmp_path: Path) -> None:
    """Spec section 15.2: the folder and all tables are created on first open."""
    state = open_state(tmp_path / ".stopline")

    rows = state.connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")

    assert {row[0] for row in rows} == TABLES
    assert state.set_aside is None
    _close(state)


def test_settings_from_the_spec(tmp_path: Path) -> None:
    """Spec section 15.2: WAL mode and busy_timeout = 2000 ms."""
    state = open_state(tmp_path)

    assert state.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert state.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 2000
    _close(state)


def test_meta_records_version_and_creation_time(tmp_path: Path) -> None:
    """Spec section 15.2: meta holds schema_version and created_at."""
    state = open_state(tmp_path)

    meta = dict(state.connection.execute("SELECT key, value FROM meta"))

    assert meta["schema_version"] == "1"
    assert meta["created_at"].endswith("+00:00")
    _close(state)


def test_data_survives_reopening(tmp_path: Path) -> None:
    """Spec section 15.2: an up-to-date database is opened, not rebuilt."""
    _note_trust(tmp_path)

    state = open_state(tmp_path)

    assert _trusted_parts(state) == ["auth"]
    assert state.set_aside is None
    _close(state)


def test_database_is_ignored_by_git_and_contracts_are_not(tmp_path: Path) -> None:
    """Spec section 15.1: state.db stays out of git, and contracts stay in it."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # noqa: S607
    _close(open_state(tmp_path / ".stopline"))
    aside = tmp_path / ".stopline" / "state.db.broken-123"

    def ignored(path: Path) -> bool:
        check = subprocess.run(  # noqa: S603
            ["git", "check-ignore", "-q", str(path)],  # noqa: S607
            cwd=tmp_path,
            check=False,
        )
        return check.returncode == 0

    assert ignored(tmp_path / ".stopline" / "state.db")
    assert ignored(tmp_path / ".stopline" / "state.db-wal")
    assert ignored(aside)
    assert ignored(tmp_path / ".stopline" / "logs" / "debug.jsonl")
    assert ignored(tmp_path / ".stopline" / "runs" / "output.txt")
    assert not ignored(tmp_path / ".stopline" / "parts" / "auth.toml")
    assert not ignored(tmp_path / ".stopline" / "config.toml")
    assert not ignored(tmp_path / ".stopline" / ".gitignore")


def test_existing_gitignore_is_left_alone(tmp_path: Path) -> None:
    """Spec section 15.1: the file is committed, so the owner may have changed it."""
    (tmp_path / ".gitignore").write_text("my rules\n", encoding="utf-8")

    _close(open_state(tmp_path))

    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "my rules\n"


def _overwrite(path: Path) -> None:
    path.write_bytes(b"this is not a database, but it is long enough to look like a file" * 100)


def _cut_short(path: Path) -> None:
    path.write_bytes(path.read_bytes()[:100])


def _flip_a_page(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[4096 : 4096 + 200] = b"\xff" * 200  # the second page holds the table of tables or data
    data[100:108] = b"\xff" * 8  # and this damages the first page's header
    path.write_bytes(bytes(data))


@pytest.mark.parametrize("damage", [_overwrite, _cut_short, _flip_a_page])
def test_damaged_database_is_set_aside_and_replaced(
    tmp_path: Path, damage: Callable[[Path], None]
) -> None:
    """Spec section 15.2: a corrupt database is renamed aside and rebuilt."""
    _note_trust(tmp_path)
    original = tmp_path / "state.db"
    damage(original)
    damaged_bytes = original.read_bytes()

    state = open_state(tmp_path)

    assert state.set_aside is not None
    assert state.set_aside.read_bytes() == damaged_bytes
    assert _trusted_parts(state) == []  # FR-CON-08: a rebuild leaves every part untrusted
    assert state.connection.execute("SELECT count(*) FROM meta").fetchone()[0] == 2
    _close(state)


@pytest.mark.parametrize("value", ["2", "not a number"])
def test_database_this_version_cannot_read_is_set_aside(tmp_path: Path, value: str) -> None:
    """Spec section 15.2: an incompatible database is renamed aside and rebuilt."""
    _note_trust(tmp_path)
    connection = sqlite3.connect(tmp_path / "state.db")
    connection.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (value,))
    connection.commit()
    connection.close()

    state = open_state(tmp_path)

    assert state.set_aside is not None
    assert state.set_aside.exists()
    assert _trusted_parts(state) == []
    _close(state)


def test_busy_database_is_never_replaced(tmp_path: Path) -> None:
    """FR-CON-08: being busy is not damage. Rebuilding here would lose every trust record."""
    _note_trust(tmp_path)
    holder = sqlite3.connect(tmp_path / "state.db", isolation_level=None)
    holder.execute("PRAGMA locking_mode = EXCLUSIVE")
    holder.execute("BEGIN EXCLUSIVE")

    with pytest.raises(StateError, match="no other program"):
        open_state(tmp_path)

    holder.execute("ROLLBACK")
    holder.close()
    assert list(tmp_path.glob("state.db.broken-*")) == []
    state = open_state(tmp_path)
    assert _trusted_parts(state) == ["auth"]
    _close(state)


def test_busy_opening_is_tried_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec section 15.2: SQLite gives up at once when WAL mode is being switched on."""
    real = db._connect_once
    calls: list[Path] = []

    def busy_the_first_time(path: Path) -> sqlite3.Connection:
        calls.append(path)
        if len(calls) == 1:
            busy = sqlite3.OperationalError("database is locked")
            busy.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise busy
        return real(path)

    monkeypatch.setattr(db, "_connect_once", busy_the_first_time)

    state = open_state(tmp_path)

    assert len(calls) == 2
    assert state.set_aside is None
    _close(state)


def test_folder_that_cannot_be_created_is_a_state_error(tmp_path: Path) -> None:
    """Spec section 13.2: a clear message, not a crash."""
    blocker = tmp_path / ".stopline"
    blocker.write_text("", encoding="utf-8")

    with pytest.raises(StateError, match="Fix: "):
        open_state(blocker)


def test_upgrade_keeps_existing_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec section 15.2: upgrades are numbered, go forward, and apply on open."""
    _note_trust(tmp_path)
    original = db._migrations()
    upgraded = {**original, 2: "CREATE TABLE extra(a); INSERT INTO extra VALUES (1);"}
    monkeypatch.setattr(db, "_migrations", lambda: upgraded)

    state = open_state(tmp_path)

    assert _trusted_parts(state) == ["auth"]
    assert state.connection.execute("SELECT a FROM extra").fetchone()[0] == 1
    version = state.connection.execute("SELECT value FROM meta WHERE key = 'schema_version'")
    assert version.fetchone()[0] == "2"
    _close(state)


def test_failed_upgrade_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec section 15.2: a bad upgrade must not leave the database half changed."""
    _note_trust(tmp_path)
    original = db._migrations()
    broken = {**original, 2: "CREATE TABLE extra(a); THIS IS NOT SQL;"}
    monkeypatch.setattr(db, "_migrations", lambda: broken)

    with pytest.raises(StateError):
        open_state(tmp_path)

    monkeypatch.setattr(db, "_migrations", lambda: original)
    state = open_state(tmp_path)
    assert _trusted_parts(state) == ["auth"]
    assert state.set_aside is None
    has_extra = state.connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'extra'")
    assert has_extra.fetchone() is None
    _close(state)


def test_many_programs_can_create_it_at_once(tmp_path: Path) -> None:
    """Spec section 15.2: safe for concurrent hook processes."""

    def open_and_close(_: int) -> bool:
        state = open_state(tmp_path)
        _close(state)
        return state.set_aside is None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(open_and_close, range(8)))

    assert all(results)
    assert list(tmp_path.glob("state.db.broken-*")) == []
