#!/usr/bin/env bash
# Deploy praxis-v2 on the Ryzen box.
# Assumes bootstrap.sh has been run once.
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/opt/praxis-v2}"
cd "$REPO_ROOT"

echo "[deploy] pulling latest"
git fetch origin
git reset --hard origin/master

echo "[deploy] syncing dependencies"
uv sync --no-dev

echo "[deploy] running migrations"
uv run alembic upgrade head

echo "[deploy] restarting services"
sudo systemctl daemon-reload
sudo systemctl restart \
  praxis-dispatcher.service \
  praxis-scheduler.service \
  praxis-mcp.service \
  praxis-dashboard.service \
  praxis-poller-edgar-8k.service \
  praxis-poller-inbox.service \
  praxis-syncer.service

echo "[deploy] status"
sleep 3
systemctl status --no-pager 'praxis-*.service' || true

echo "[deploy] done"
