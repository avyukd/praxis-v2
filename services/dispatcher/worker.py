from __future__ import annotations

import asyncio
import traceback
import uuid
from pathlib import Path

from sqlalchemy import select

from praxis_core.config import get_settings
from praxis_core.db.models import Task
from praxis_core.db.session import session_scope
from praxis_core.llm.invoker import LLMResult
from praxis_core.llm.rate_limit import RateLimitManager
from praxis_core.logging import get_logger
from praxis_core.observability.cost import record_task_telemetry
from praxis_core.observability.events import emit_event
from praxis_core.schemas.task_types import TaskType
from praxis_core.tasks.lifecycle import (
    extend_lease,
    mark_dead_letter,
    mark_failed,
    mark_partial,
    mark_success,
    release_task,
    requeue_on_rate_limit,
)
from praxis_core.tasks.validators import get_validator

from handlers import HandlerContext, get_handler_registry

log = get_logger("dispatcher.worker")


class WorkerCancelled(Exception):
    pass


async def _heartbeat_loop(task_id: uuid.UUID, worker_id: str, stop: asyncio.Event) -> None:
    settings = get_settings()
    interval = max(10, settings.worker_heartbeat_interval_s)
    while not stop.is_set():
        try:
            async with session_scope() as session:
                ok = await extend_lease(session, task_id, worker_id)
                if not ok:
                    log.warning("worker.lease_lost", task_id=str(task_id))
                    stop.set()
                    return
        except Exception as e:
            log.warning("worker.heartbeat_fail", task_id=str(task_id), error=str(e))
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def execute_task(task: Task, worker_id: str) -> None:
    """Run a single task to completion. Caller must have already claimed it.

    On rate-limit: records hit, requeues task.
    On partial: marks partial, enqueues nothing by default (handlers are responsible
      for richer remediation if they want).
    On success: validates artifacts, marks success.
    On exception: increments attempts; if >=max_attempts, dead-letters.
    """
    settings = get_settings()
    registry = get_handler_registry()
    handler = registry.get(task.type)
    if handler is None:
        async with session_scope() as session:
            await mark_failed(session, task.id, f"no handler registered for task type {task.type}")
        return

    stop = asyncio.Event()
    hb_task = asyncio.create_task(_heartbeat_loop(task.id, worker_id, stop))
    rate_limiter = RateLimitManager()

    ctx = HandlerContext(
        task_id=str(task.id),
        task_type=task.type,
        payload=task.payload,
        vault_root=settings.vault_root,
        model=task.model,
    )

    try:
        await emit_event(
            "dispatcher.worker",
            "task_start",
            {"task_id": str(task.id), "type": task.type, "worker_id": worker_id},
        )
        result = await asyncio.wait_for(
            handler(ctx), timeout=max(60, settings.cli_wall_clock_timeout_s + 120)
        )
    except asyncio.TimeoutError:
        stop.set()
        await hb_task
        async with session_scope() as session:
            await _handle_failure(session, task, "handler wall-clock timeout")
        return
    except Exception as e:
        stop.set()
        await hb_task
        async with session_scope() as session:
            await _handle_failure(session, task, f"{type(e).__name__}: {e}\n{traceback.format_exc()[:1500]}")
        return

    # Handler exited normally; handle its signaling
    stop.set()
    await hb_task

    llm: LLMResult | None = result.llm_result
    async with session_scope() as session:
        if llm is not None:
            await record_task_telemetry(session, task.id, llm)
        if result.message == "rate_limit" or (llm and llm.finish_reason == "rate_limit"):
            await rate_limiter.record_hit(session)
            await requeue_on_rate_limit(session, task.id)
            await emit_event(
                "dispatcher.worker",
                "task_rate_limit",
                {"task_id": str(task.id), "type": task.type},
            )
            return

        if not result.ok:
            await _handle_failure(session, task, result.message or "handler returned ok=False")
            return

        validator = get_validator(task.type)
        if validator is None:
            await mark_success(session, task.id)
            await emit_event(
                "dispatcher.worker",
                "task_success",
                {"task_id": str(task.id), "type": task.type, "validation": "skipped"},
            )
            return

        validation = validator(task.payload, settings.vault_root)
        telemetry = None  # already recorded above

        if validation.is_success:
            await mark_success(session, task.id, validation=validation)
            await emit_event(
                "dispatcher.worker",
                "task_success",
                {
                    "task_id": str(task.id),
                    "type": task.type,
                    "ok": validation.ok,
                },
            )
            # If this task is a rate-limit probe: clear rate limit
            if task.type == TaskType.RATE_LIMIT_PROBE.value:
                await rate_limiter.probe_succeeded(session)
            return

        if validation.is_partial:
            await mark_partial(session, task.id, validation)
            await emit_event(
                "dispatcher.worker",
                "task_partial",
                {
                    "task_id": str(task.id),
                    "type": task.type,
                    "missing": validation.missing,
                    "malformed": [m.model_dump() for m in validation.malformed],
                },
            )
            return

        # validation all-missing — treat as failure
        await _handle_failure(
            session, task, f"artifacts missing: {validation.missing}"
        )


async def _handle_failure(session, task: Task, error: str) -> None:
    if task.attempts >= task.max_attempts:
        await mark_dead_letter(session, task.id, error)
        await emit_event(
            "dispatcher.worker",
            "task_dead_letter",
            {"task_id": str(task.id), "type": task.type, "error": error[:500]},
        )
    else:
        await mark_failed(session, task.id, error)
        # Re-queue as 'queued' again for retry (next dispatch will pick up)
        from sqlalchemy import text

        await session.execute(
            text(
                "UPDATE tasks SET status = 'queued', lease_holder = NULL, "
                "lease_expires_at = NULL, last_error = :err WHERE id = :tid"
            ),
            {"err": error[:2000], "tid": task.id},
        )
        await emit_event(
            "dispatcher.worker",
            "task_retry_scheduled",
            {"task_id": str(task.id), "type": task.type, "attempts": task.attempts, "error": error[:500]},
        )
