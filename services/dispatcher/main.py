from __future__ import annotations

import asyncio
import signal
import uuid

from praxis_core.config import get_settings
from praxis_core.db.session import session_scope
from praxis_core.llm.invoker import require_claude_cli
from praxis_core.llm.rate_limit import RateLimitManager
from praxis_core.logging import configure_logging, get_logger
from praxis_core.observability.events import emit_event
from praxis_core.observability.heartbeat import beat
from praxis_core.observability.sd_notify import notify_ready, notify_stopping, notify_watchdog
from praxis_core.schemas.task_types import TaskModel, TaskType
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.tasks.lifecycle import claim_next_task
from praxis_core.time_et import et_iso, now_utc
from services.dispatcher.pool import WorkerPool
from services.dispatcher.worker import execute_task

log = get_logger("dispatcher.main")


async def _maybe_launch_probe(pool: WorkerPool) -> None:
    """If rate-limit state is 'limited' and expired, try to launch a probe."""
    rate_limiter = RateLimitManager()
    async with session_scope() as session:
        snap = await rate_limiter.snapshot(session)
        if snap.status != "limited":
            return
        if snap.limited_until_ts is None or snap.limited_until_ts > now_utc():
            return

        probe_id = uuid.uuid4()
        transitioned = await rate_limiter.try_transition_to_probing(session, probe_id)
        if not transitioned:
            return

        # Enqueue the actual probe task (Haiku synthetic)
        await enqueue_task(
            session,
            task_type=TaskType.RATE_LIMIT_PROBE,
            payload={},
            priority=0,
            dedup_key=f"rate_limit_probe:{probe_id}",
            model=TaskModel.HAIKU,
            max_attempts=1,
        )
        log.info("dispatcher.probe_launched", probe_id=str(probe_id))


async def _dispatch_tick(pool: WorkerPool) -> int:
    rate_limiter = RateLimitManager()

    # 1. Maybe launch probe if limited + expired
    await _maybe_launch_probe(pool)

    # 2. Check rate-limit state
    async with session_scope() as session:
        can_dispatch, snap = await rate_limiter.can_dispatch(session)

    if not can_dispatch and snap.status != "probing":
        return 0

    # 3. Fill available slots, respecting resource locks. In probing state,
    # only claim rate_limit_probe tasks — everything else waits for the
    # probe to flip state back to 'clear'. Before this gate, probes got
    # enqueued by _maybe_launch_probe but never claimed, so the system
    # stayed stuck in 'probing' forever after each real rate-limit hit.
    probing = snap.status == "probing"
    allowed_types = ["rate_limit_probe"] if probing else None

    assigned = 0
    available = pool.available_slots()
    if available <= 0:
        return 0

    # Serialize probes: one at a time. Without this, if multiple probe
    # tasks are queued, the claim loop fires up to pool_size probes in
    # parallel and they all hit upstream RL at the same instant.
    if probing:
        available = 1

    for _ in range(available):
        excluded_resources = pool.running_resource_keys()
        worker_id = pool.alloc_worker_id()
        async with session_scope() as session:
            task = await claim_next_task(
                session,
                worker_id=worker_id,
                excluded_resource_keys=excluded_resources or None,
                allowed_types=allowed_types,
            )
            if task is None:
                break

        await pool.submit(task, execute_task(task, worker_id), worker_id=worker_id)
        assigned += 1

    return assigned


async def run_loop() -> None:
    configure_logging()
    settings = get_settings()
    if settings.praxis_invoker == "cli":
        claude_path = require_claude_cli()
    else:
        claude_path = None
    log.info(
        "dispatcher.start",
        pool_size=settings.dispatcher_pool_size,
        tick_s=settings.dispatcher_tick_interval_s,
        invoker=settings.praxis_invoker,
        claude_path=claude_path,
    )

    pool = WorkerPool(size=settings.dispatcher_pool_size)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    await emit_event(
        "dispatcher.main",
        "started",
        {"pool_size": settings.dispatcher_pool_size, "invoker": settings.praxis_invoker},
    )
    notify_ready()

    tick_count = 0
    while not stop_event.is_set():
        tick_count += 1
        try:
            assigned = await _dispatch_tick(pool)
            notify_watchdog()
            await beat(
                "dispatcher.main",
                status={
                    "last_tick_at": et_iso(),
                    "running": len(pool.running_tasks()),
                    "available_slots": pool.available_slots(),
                    "assigned_this_tick": assigned,
                    "tick_count": tick_count,
                },
            )
        except Exception as e:
            log.exception("dispatcher.tick_fail", error=str(e))
            await beat(
                "dispatcher.main",
                status={
                    "last_tick_at": et_iso(),
                    "error": str(e)[:200],
                    "tick_count": tick_count,
                },
            )

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.dispatcher_tick_interval_s)
        except TimeoutError:
            pass

    log.info("dispatcher.shutdown", draining=len(pool.running_tasks()))
    notify_stopping()
    await pool.drain(timeout_s=30)
    await emit_event("dispatcher.main", "shutdown", {"tick_count": tick_count})
    log.info("dispatcher.done")


def main() -> None:
    asyncio.run(run_loop())


if __name__ == "__main__":
    main()
