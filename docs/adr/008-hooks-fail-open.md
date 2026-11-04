# ADR 008: Hooks fail open

- Status: accepted
- Date: 2026-11-03 (spec v2.0, section 18.3)

## Context and problem statement

A guard that crashes or blocks an agent session by accident gets uninstalled.

## Considered options

- Fail open: any internal error exits 0 with a short warning
- Fail closed: any internal error blocks

## Decision outcome

Fail open (NFR-04). Only a deliberate blocking finding may stop an agent.

## Consequences

- Fault-injection tests must prove that missing git, a corrupt database, timeouts and huge inputs all exit 0.
