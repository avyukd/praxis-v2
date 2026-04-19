#!/usr/bin/env bash
# End-to-end smoke test: enqueue a refresh_index task, wait for it to complete,
# bail loudly if anything is broken. Uses only non-LLM tasks so it's cheap + deterministic.
#
# Run this after bootstrap.sh on Ryzen before depending on the system for trading.
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="python"
fi

echo "[smoke] 1/4 — checking dispatcher is heartbeating"
AGE=$("$PYTHON" <<'PY'
import asyncio
from praxis_core.db.session import session_scope
from sqlalchemy import select
from praxis_core.db.models import Heartbeat

async def main():
    async with session_scope() as s:
        hb = (await s.execute(select(Heartbeat).where(Heartbeat.component == "dispatcher.main"))).scalar_one_or_none()
        if hb is None:
            print("MISSING")
            return
        from datetime import datetime, timezone
        age = (datetime.now(timezone.utc) - hb.last_heartbeat).total_seconds()
        print(int(age))

asyncio.run(main())
PY
)
if [[ "$AGE" == "MISSING" ]]; then
  echo "[smoke] FAIL: dispatcher has never heartbeated. Is it running?"
  exit 1
fi
if (( AGE > 120 )); then
  echo "[smoke] FAIL: dispatcher heartbeat is stale (${AGE}s old)"
  exit 1
fi
echo "[smoke]     dispatcher heartbeat age: ${AGE}s OK"

echo "[smoke] 2/4 — enqueueing refresh_index probe task"
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
echo "[smoke]     enqueued task $TASK_ID"

echo "[smoke] 3/4 — waiting up to 60s for task to complete"
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
  echo "[smoke]     status=$STATUS"
  case "$STATUS" in
    success)
      echo "[smoke] 4/4 — PASS: refresh_index completed successfully"
      exit 0
      ;;
    failed|dead_letter|canceled)
      echo "[smoke] FAIL: task status=$STATUS"
      exit 1
      ;;
  esac
  sleep 3
done

echo "[smoke] FAIL: task did not complete within 60s"
exit 1
