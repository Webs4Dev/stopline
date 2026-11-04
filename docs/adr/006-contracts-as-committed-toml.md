# ADR 006: Contracts as committed TOML

- Status: accepted
- Date: 2026-11-03 (spec v2.0, section 18.3)

## Context and problem statement

Part contracts define what an agent may build. They must be readable by people, reviewable in pull requests and shareable across a team.

## Considered options

- TOML files in `.stopline/parts/`, committed to git
- JSON files
- A database

## Decision outcome

Committed TOML, one file per part (spec section 9.1), with a published JSON Schema for editor validation.

## Consequences

- Status and baseline live in a committed file, so they are checked against a local trust record in state.db (FR-CON-08).
