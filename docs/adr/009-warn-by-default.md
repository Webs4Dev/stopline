# ADR 009: Warn by default, enforce opt-in

- Status: accepted
- Date: 2026-11-03 (spec v2.0, section 18.3; owner confirmed 2026-11-04, Q3)

## Context and problem statement

Stopline must earn trust before it blocks anything.

## Considered options

- warn by default, enforce opt-in
- enforce by default

## Decision outcome

Default mode is `warn`: feedback to the agent, never a block. Enforce is documented as recommended after about a week of warn mode.

## Consequences

- Users who want blocking must set `mode = "enforce"` in `.stopline/config.toml`.
