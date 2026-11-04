# ADR 005: msgspec over pydantic

- Status: accepted
- Date: 2026-11-03 (spec v2.0, section 18.3)

## Context and problem statement

Data models are decoded on every hook call, so import time and decode speed matter. Hook input must also be decoded strictly.

## Considered options

- msgspec
- pydantic v2 (heavier import)
- dataclasses (no validation)

## Decision outcome

msgspec. It has fast import and decode, strict validation and JSON Schema generation.

## Consequences

- msgspec is a runtime dependency (spec section 18.1).
- Contract and report JSON Schemas are generated from the models.
