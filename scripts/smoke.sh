#!/usr/bin/env bash
# End-to-end smoke test implementing the D73 IRL sequence.
# Default run covers the cheap/fast checks (1-3, 7). Pass --full to
# additionally run the LLM-backed and long-running steps (4-6, 8).
#
# Exits 0 only if all requested steps pass.
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="python"
fi

MODE="quick"
if [[ "${1:-}" == "--full" ]]; then
  MODE="full"
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'
ok() { echo -e "${GREEN}[smoke ok]${NC} $1"; }
bad() { echo -e "${RED}[smoke FAIL]${NC} $1"; exit 1; }
step() { echo -e "${YELLOW}[smoke]${NC} $1"; }

# ─── D73.1 heartbeat ───────────────────────────────────────────────────
step "1/7 — dispatcher heartbeat fresh (<120s)"
AGE=$("$PYTHON" <<'PY'
import asyncio
from datetime import UTC, datetime
from sqlalchemy import select
from praxis_core.db.session import session_scope
from praxis_core.db.models import Heartbeat

async def main():
    async with session_scope() as s:
        hb = (await s.execute(
            select(Heartbeat).where(Heartbeat.component == "dispatcher.main")
        )).scalar_one_or_none()
        if hb is None:
            print("MISSING"); return
        print(int((datetime.now(UTC) - hb.last_heartbeat).total_seconds()))

asyncio.run(main())
PY
)
[[ "$AGE" == "MISSING" ]] && bad "dispatcher never heartbeated"
(( AGE > 120 )) && bad "dispatcher heartbeat stale (${AGE}s)"
ok "dispatcher heartbeat ${AGE}s"

# ─── D73.3 rate-limit state (moved up — gates dispatch) ────────────────
step "2/7 — rate-limit state"
RL=$("$PYTHON" <<'PY'
import asyncio
from praxis_core.db.session import session_scope
from praxis_core.llm.rate_limit import RateLimitManager

async def main():
    async with session_scope() as s:
        snap = await RateLimitManager().snapshot(s)
        print(snap.status)

asyncio.run(main())
PY
)
if [[ "$RL" == "clear" ]]; then
  ok "rate_limit_state=clear"
  RL_CLEAR=1
else
  echo -e "${YELLOW}[smoke warn]${NC} rate_limit_state=$RL — skipping dispatch-gated steps"
  RL_CLEAR=0
fi

# ─── D73.2 cheap probe task (gated on rate-limit clear) ────────────────
step "3/7 — enqueue + drain refresh_index probe"
if (( RL_CLEAR == 0 )); then
  echo -e "${YELLOW}[smoke skip]${NC} rate_limit not clear"
else
TASK_ID=$("$PYTHON" <<'PY'
import asyncio, uuid
from praxis_core.db.session import session_scope
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.schemas.task_types import TaskType

async def main():
    async with session_scope() as s:
        tid = await enqueue_task(
            s,
            task_type=TaskType.REFRESH_INDEX,
            payload={"scope": "incremental", "triggered_by": "smoke"},
            priority=0,
            dedup_key=f"smoke:{uuid.uuid4()}",
        )
        print(str(tid))

asyncio.run(main())
PY
)
DEADLINE=$(( $(date +%s) + 60 ))
while (( $(date +%s) < DEADLINE )); do
  STATUS=$("$PYTHON" <<PY
import asyncio, uuid
from praxis_core.db.session import session_scope
from praxis_core.db.models import Task

async def main():
    async with session_scope() as s:
        t = await s.get(Task, uuid.UUID("$TASK_ID"))
        print(t.status if t else "MISSING")

asyncio.run(main())
PY
)
  case "$STATUS" in
    success) ok "refresh_index task $TASK_ID success"; break ;;
    failed|dead_letter|canceled) bad "refresh_index task $TASK_ID → $STATUS" ;;
  esac
  sleep 3
done
[[ "$STATUS" == "success" ]] || bad "refresh_index timed out (status=$STATUS)"
fi

# ─── D73.7 surface_ideas_now ───────────────────────────────────────────
step "4/7 — manual surface_ideas trigger"
if (( RL_CLEAR == 0 )); then
  echo -e "${YELLOW}[smoke skip]${NC} rate_limit not clear"
else
SURF_ID=$("$PYTHON" <<'PY'
import asyncio
from praxis_core.db.session import session_scope
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.schemas.task_types import TaskType, TaskModel
from praxis_core.time_et import now_et

async def main():
    async with session_scope() as s:
        tid = await enqueue_task(
            s,
            task_type=TaskType.SURFACE_IDEAS,
            payload={"triggered_by": "smoke", "manual": True},
            priority=1,
            model=TaskModel.SONNET,
            dedup_key=f"surface_ideas:smoke:{now_et().strftime('%Y%m%d%H%M%S')}",
            max_attempts=1,
        )
        print(str(tid))

asyncio.run(main())
PY
)
# Surface run may take 1-2 min; don't block on full completion unless --full
if [[ "$MODE" == "full" ]]; then
  DEADLINE=$(( $(date +%s) + 180 ))
  while (( $(date +%s) < DEADLINE )); do
    STATUS=$("$PYTHON" <<PY
import asyncio, uuid
from praxis_core.db.session import session_scope
from praxis_core.db.models import Task

async def main():
    async with session_scope() as s:
        t = await s.get(Task, uuid.UUID("$SURF_ID"))
        print(t.status if t else "MISSING")

asyncio.run(main())
PY
)
    [[ "$STATUS" == "success" ]] && { ok "surface_ideas $SURF_ID success"; break; }
    case "$STATUS" in
      failed|dead_letter|canceled) bad "surface_ideas → $STATUS" ;;
    esac
    sleep 5
  done
  [[ "$STATUS" == "success" ]] || bad "surface_ideas timed out"
else
  ok "surface_ideas enqueued ($SURF_ID) — not waiting (quick mode)"
fi
fi

# ─── D73.8 MCP control plane (list_investigations only, non-destructive) ──
step "5/7 — MCP list_investigations"
INV_COUNT=$("$PYTHON" <<'PY'
import asyncio
from sqlalchemy import select, func
from praxis_core.db.session import session_scope
from praxis_core.db.models import Investigation

async def main():
    async with session_scope() as s:
        n = (await s.execute(select(func.count()).select_from(Investigation))).scalar_one()
        print(n)

asyncio.run(main())
PY
)
ok "investigations table reachable (n=$INV_COUNT)"

# ─── Full-only steps (gate: --full) ────────────────────────────────────
if [[ "$MODE" == "full" ]]; then
  step "6/7 — EDGAR poller --once"
  if "$PYTHON" -m services.pollers.edgar_8k --once 2>&1 | tail -10; then
    ok "edgar_8k --once completed"
  else
    bad "edgar_8k --once failed"
  fi

  step "7/7 — press pollers --once"
  "$PYTHON" -m services.pollers.press_us --once 2>&1 | tail -5
  ok "press_us --once completed"
  "$PYTHON" -m services.pollers.press_ca --once 2>&1 | tail -5
  ok "press_ca --once completed"
fi

echo
echo -e "${GREEN}Smoke PASS (mode=$MODE)${NC}"
