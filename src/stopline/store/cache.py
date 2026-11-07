# The feature cache in state.db (spec FR-EX-08 and section 15.2): features already extracted
# from a file's exact contents, so an unchanged file is never parsed twice.
import sqlite3
from datetime import UTC, datetime, timedelta

_TOUCH_AFTER = timedelta(days=1)  # a hit refreshes last_used at most once a day
_UNUSED_FOR = timedelta(days=30)
MAX_BYTES = 200 * 1024 * 1024


def get(
    connection: sqlite3.Connection, blob_sha: str, extractor: str, queries: str, now: datetime
) -> bytes | None:
    """Return the stored features for this blob, extractor version and query-set hash."""
    row = connection.execute(
        "SELECT features, last_used FROM feature_cache"
        " WHERE blob_sha = ? AND extractor = ? AND queries = ?",
        (blob_sha, extractor, queries),
    ).fetchone()
    if row is None:
        return None
    features, last_used = row
    if last_used is None or last_used < _stamp(now - _TOUCH_AFTER):
        connection.execute(
            "UPDATE feature_cache SET last_used = ?"
            " WHERE blob_sha = ? AND extractor = ? AND queries = ?",
            (_stamp(now), blob_sha, extractor, queries),
        )
    return bytes(features)


def put(
    connection: sqlite3.Connection,
    blob_sha: str,
    extractor: str,
    queries: str,
    path: str,
    features: bytes,
    now: datetime,
) -> None:
    """Store features for this blob, replacing any older entry with the same key."""
    connection.execute(
        "INSERT OR REPLACE INTO feature_cache VALUES (?, ?, ?, ?, ?, ?)",
        (blob_sha, extractor, queries, path, features, _stamp(now)),
    )


def evict(connection: sqlite3.Connection, now: datetime, max_bytes: int = MAX_BYTES) -> int:
    """Forget old entries (spec section 15.2) and return how many went.

    First every entry unused for 30 days, then the least recently used ones while the
    features together are larger than `max_bytes`.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        stale = connection.execute(
            "DELETE FROM feature_cache WHERE last_used IS NULL OR last_used < ?",
            (_stamp(now - _UNUSED_FOR),),
        ).rowcount
        # Keep the most recently used entries until their running total passes the limit.
        oversize = connection.execute(
            "DELETE FROM feature_cache WHERE rowid IN ("
            " SELECT rowid FROM (SELECT rowid, SUM(length(features))"
            "  OVER (ORDER BY last_used DESC, rowid DESC) AS running FROM feature_cache)"
            " WHERE running > ?)",
            (max_bytes,),
        ).rowcount
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    return stale + oversize


def _stamp(moment: datetime) -> str:
    # One fixed format in UTC, so comparing the text compares the times.
    return moment.astimezone(UTC).isoformat(timespec="seconds")
