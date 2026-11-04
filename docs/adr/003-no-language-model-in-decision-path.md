# ADR 003: No language model in the decision path

- Status: accepted
- Date: 2026-11-03 (spec v2.0, section 18.3)

## Context and problem statement

Stopline blocks an agent when a part is complete or when unplanned features appear. A wrong block erodes trust quickly.

## Considered options

- Deterministic matching only
- A language model to judge whether a feature is planned

## Decision outcome

Deterministic matching only. This gives determinism, no false accusations from model variance, offline use and zero cost.

## Consequences

- A future suggestion engine may propose which item a feature belongs to, but it can only suggest and never changes a verdict (spec section 11.1).
