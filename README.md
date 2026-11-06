# Stopline

A local guard that tells AI coding agents when a part of the architecture is finished, flags features nobody asked for, and keeps the work inside the plan.

Status: early development (milestone M1 finished: contracts and the `stopline part` commands; nothing watches an agent yet). See `docs/specs/stopline-spec.md` for the full specification.

## Development setup

The dev environment lives in `venv/` at the repo root.

```bash
export UV_PROJECT_ENVIRONMENT=venv
uv sync
uv run stopline --version
uv run pytest
```
