# ADR 002: tree-sitter for all languages

- Status: accepted
- Date: 2026-11-03 (spec v2.0, section 18.3)

## Context and problem statement

Stopline extracts features from Python, TypeScript and JavaScript, often from files that are half-written mid-edit.

## Considered options

- tree-sitter with one query file per language and kind
- Python `ast` (Python only, fails on syntax errors)
- Language servers (heavy)

## Decision outcome

tree-sitter for all languages. It is error-tolerant on half-written code, and one query-driven model lets contributors add a framework by adding query files only (FR-EX-01).

## Consequences

- Grammar packages are runtime dependencies (see spec section 18.1).
- Query files are gated by a golden corpus (FR-EX-09).
