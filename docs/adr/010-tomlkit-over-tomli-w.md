# ADR 010: tomlkit over tomli-w

- Status: accepted
- Date: 2026-11-04 (spec v2.1, sections 18.1 and 18.3)

## Context and problem statement

Stopline rewrites contract files, for example when `part keep` adds an approved extra. FR-CON-07 says writes should preserve comments outside Stopline-managed tables. Contracts also start with a `#:schema` comment that editors use to validate them. tomli-w, the original choice, writes from plain data and drops every comment.

## Considered options

- tomlkit (keeps comments and formatting)
- tomli-w (smaller and simpler, but drops all comments)

## Decision outcome

tomlkit. It is the only considered option that can meet FR-CON-07, and spec section 18.1 already named it as the fallback for that case.

## Consequences

- tomlkit is a runtime dependency in place of tomli-w, so the dependency count in NFR-12 is unchanged.
- tomlkit is imported only when writing. Reading still uses stdlib tomllib, so hook startup is not affected.
