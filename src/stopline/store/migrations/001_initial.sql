-- The first version of state.db (spec section 15.2). Statements here are split on semicolons,
-- so a semicolon may only end a statement.
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE feature_cache(
    blob_sha TEXT, extractor TEXT, queries TEXT, path TEXT,
    features BLOB,
    PRIMARY KEY (blob_sha, extractor, queries)
);

CREATE TABLE part_index(
    part_id TEXT PRIMARY KEY, status TEXT, baseline TEXT,
    approval_hash TEXT, completed_tree TEXT, updated_at TEXT
);

CREATE TABLE check_ledger(
    id INTEGER PRIMARY KEY, ts TEXT, session TEXT, agent TEXT,
    event TEXT, part_id TEXT, verdict TEXT, findings TEXT,
    duration_ms INTEGER, tree_hash TEXT
);

CREATE TABLE item_runs(
    part_id TEXT, item_id TEXT, tree_hash TEXT, exit_code INTEGER,
    duration_ms INTEGER, ts TEXT,
    PRIMARY KEY (part_id, item_id, tree_hash)
);

CREATE TABLE block_history(
    session TEXT, fingerprint TEXT, count INTEGER, last_ts TEXT,
    PRIMARY KEY (session, fingerprint)
);

CREATE TABLE trust(
    part_id TEXT PRIMARY KEY, approval_hash TEXT, status TEXT, baseline TEXT,
    trusted_by TEXT, trusted_at TEXT
);
