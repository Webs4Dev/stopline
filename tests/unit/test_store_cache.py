# Tests for the feature cache (spec FR-EX-08 and section 15.2).
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from stopline.store import cache
from stopline.store.db import open_state

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_state(tmp_path / ".stopline").connection
    yield connection
    connection.close()


def _last_used(db: sqlite3.Connection, blob: str) -> str:
    row = db.execute("SELECT last_used FROM feature_cache WHERE blob_sha = ?", (blob,)).fetchone()
    return str(row[0])


def _blobs(db: sqlite3.Connection) -> list[str]:
    return sorted(row[0] for row in db.execute("SELECT blob_sha FROM feature_cache"))


def test_stored_features_come_back(db: sqlite3.Connection) -> None:
    """FR-EX-08: the cache key is blob, extractor version and query-set hash."""
    cache.put(db, "b1", "py-1", "q1", "src/a.py", b"features", NOW)

    assert cache.get(db, "b1", "py-1", "q1", NOW) == b"features"
    assert cache.get(db, "b1", "py-2", "q1", NOW) is None
    assert cache.get(db, "b1", "py-1", "q2", NOW) is None
    assert cache.get(db, "b2", "py-1", "q1", NOW) is None


def test_storing_again_replaces_the_entry(db: sqlite3.Connection) -> None:
    """FR-EX-08: one entry per key."""
    cache.put(db, "b1", "py-1", "q1", "src/a.py", b"old", NOW)
    cache.put(db, "b1", "py-1", "q1", "src/b.py", b"new", NOW)

    assert cache.get(db, "b1", "py-1", "q1", NOW) == b"new"
    assert _blobs(db) == ["b1"]


def test_a_hit_refreshes_last_used_at_most_once_a_day(db: sqlite3.Connection) -> None:
    """Spec 15.2: last_used is updated on a hit, but not on every hit."""
    cache.put(db, "b1", "py-1", "q1", "a.py", b"f", NOW)

    cache.get(db, "b1", "py-1", "q1", NOW + timedelta(hours=23))
    assert _last_used(db, "b1") == "2026-11-07T12:00:00+00:00"

    cache.get(db, "b1", "py-1", "q1", NOW + timedelta(days=1, seconds=1))
    assert _last_used(db, "b1") == "2026-11-08T12:00:01+00:00"


def test_times_in_other_zones_are_stored_in_utc(db: sqlite3.Connection) -> None:
    """Spec 15.2: one format, so comparing stored times as text is correct."""
    india = timezone(timedelta(hours=5, minutes=30))
    cache.put(db, "b1", "py-1", "q1", "a.py", b"f", datetime(2026, 11, 7, 23, 0, tzinfo=india))

    assert _last_used(db, "b1") == "2026-11-07T17:30:00+00:00"


def test_entries_unused_for_30_days_are_evicted(db: sqlite3.Connection) -> None:
    """Spec 15.2: an entry kept in use survives; one left alone for 30 days goes."""
    cache.put(db, "old", "py-1", "q1", "a.py", b"f", NOW - timedelta(days=40))
    cache.put(db, "used", "py-1", "q1", "b.py", b"f", NOW - timedelta(days=40))
    cache.get(db, "used", "py-1", "q1", NOW - timedelta(days=2))
    cache.put(db, "recent", "py-1", "q1", "c.py", b"f", NOW - timedelta(days=29))

    assert cache.evict(db, NOW) == 1
    assert _blobs(db) == ["recent", "used"]


def test_least_recently_used_go_first_when_over_the_size_limit(db: sqlite3.Connection) -> None:
    """Spec 15.2: over the size limit, the oldest-used entries are dropped until it fits."""
    for day, blob in enumerate(["oldest", "middle", "newest"]):
        cache.put(db, blob, "py-1", "q1", f"{blob}.py", b"x" * 100, NOW + timedelta(days=day))

    assert cache.evict(db, NOW + timedelta(days=3), max_bytes=250) == 1
    assert _blobs(db) == ["middle", "newest"]
    assert cache.evict(db, NOW + timedelta(days=3), max_bytes=250) == 0


def test_nothing_is_evicted_when_all_is_recent_and_small(db: sqlite3.Connection) -> None:
    """Spec 15.2: eviction only removes what the two rules name."""
    cache.put(db, "b1", "py-1", "q1", "a.py", b"f", NOW)

    assert cache.evict(db, NOW) == 0
    assert _blobs(db) == ["b1"]
