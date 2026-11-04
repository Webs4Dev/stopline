# ADR 001: Python, not Rust or Go

- Status: accepted
- Date: 2026-11-03 (spec v2.0, section 18.3)

## Context and problem statement

Stopline must start fast enough to run inside agent hooks (cold start under 150 ms, Stop hook p95 under 1.5 s) and must be quick to build and maintain by one developer.

## Considered options

- Python 3.11-3.14
- Rust (fastest, single binary, slower to build)
- Go
- TypeScript

## Decision outcome

Python. A prototype measured a cold start of 100-135 ms including parser load, which meets the hook budgets. Python has mature tree-sitter bindings, is the owner's strongest language, and allows faster iteration.

## Consequences

- Revisit if NFR-01 is missed: move the hot path to Rust via PyO3.
- Users need Python available; `uv tool install stopline` covers this.
