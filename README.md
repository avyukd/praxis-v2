# praxis-v2

AI-managed investment research system. See `PLAN.md` for the full design doc.

## Quick start (local dev on Air)

```bash
uv sync
cp .env.example .env
createdb praxis
alembic upgrade head
overmind start
```

Dashboard at http://localhost:8080.

## Production (Ryzen)

```bash
sudo bash infra/bootstrap.sh      # one-time
bash infra/deploy.sh              # each deploy
```

## Layout

- `praxis_core/` — shared library (DB, vault, LLM, task lifecycle)
- `handlers/` — one file per task type
- `services/` — long-running processes (dispatcher, pollers, MCP, dashboard, etc.)
- `infra/` — systemd units, bootstrap + deploy scripts
- `alembic/` — DB migrations
- `tests/` — unit + integration tests
