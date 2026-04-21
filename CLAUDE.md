# praxis-v2 project conventions

This is a Python 3.13 monorepo for a reliable AI-managed investment research system.

## Coding behavior (from andrej-karpathy-skills/CLAUDE.md)

These bias toward caution over speed. For trivial tasks, use judgment.

### Think before coding
Don't assume. Don't hide confusion. Surface tradeoffs.
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### Simplicity first
Minimum code that solves the problem. Nothing speculative.
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.
- Would a senior engineer say this is overcomplicated? If yes, simplify.

### Surgical changes
Touch only what you must. Clean up only your own mess.
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.
- Every changed line should trace directly to the user's request.

### Goal-driven execution
Define success criteria. Loop until verified.
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan with verify steps:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria
("make it work") require constant clarification.

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
