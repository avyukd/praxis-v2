# praxis-v2 project conventions

This is a Python 3.13 monorepo for a reliable AI-managed investment research system.

## Core rules

- All database state in Postgres, accessed via SQLAlchemy 2.x async. No pickle, no JSON state files.
- All vault writes via `praxis_core.vault.writer.atomic_write()` (tempfile + rename). Never `open(path, 'w')` directly on vault files.
- No broad `except Exception:` that swallows errors. Let exceptions escape to the supervisor boundary.
- Every subprocess gets a hard wall-clock timeout with SIGKILL fallback.
- Every HTTP call gets timeout + retry + backoff (use `tenacity`).
- Never pass `ANTHROPIC_API_KEY` or `CLAUDE_API_KEY` to a `claude -p` subprocess. Strip from env before spawning.
- Imports at module top only. No inline imports inside functions.
- Pydantic for all typed data crossing process boundaries.

## Style

- Structured logging via `structlog`. Never `print()` outside of CLI handlers.
- Prefer dataclasses / Pydantic models over dicts.
- Test with pytest + pytest-asyncio. Integration tests hit real Postgres (not mocks) via pytest-postgresql.
- No comments unless WHY is non-obvious. Well-named identifiers over docstrings.

## Load-bearing invariant

Any process can be killed at any instant without corrupting shared state. This drives every design choice.

## Running locally (Air dev)

```
cp .env.example .env
# edit .env as needed
uv sync
alembic upgrade head
overmind start  # or individual: uv run python -m services.dispatcher.main
```

## Running on Ryzen

See `infra/bootstrap.sh` and `infra/deploy.sh`. systemd units in `infra/systemd/`.
