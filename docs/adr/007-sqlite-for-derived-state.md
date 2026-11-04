# ADR 007: SQLite for derived state

- Status: accepted
- Date: 2026-11-03 (spec v2.0, section 18.3; trust wording updated 2026-11-04, spec v2.1)

## Context and problem statement

Stopline needs a feature cache, a check ledger, block history and trust records. Several hook processes can run at the same time.

## Considered options

- SQLite (standard library) in WAL mode
- JSON files (no concurrency safety)
- DuckDB (not needed)

## Decision outcome

SQLite in `.stopline/state.db`, ignored by git. It is concurrency-safe, needs no install and can be rebuilt.

## Consequences

- Nothing authoritative lives there except trust records (FR-CON-08).
- After a rebuild every part is untrusted until the owner runs `stopline trust`.
