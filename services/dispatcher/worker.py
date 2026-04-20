from __future__ import annotations

import asyncio
import traceback
import uuid

from handlers import HandlerContext, HandlerResult, get_handler_registry
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
    requeue_on_rate_limit,
)
from praxis_core.tasks.validators import get_validator

log = get_logger("dispatcher.worker")

# If a task has bounced on rate limits this many times, dead-letter it.
RATE_LIMIT_BOUNCE_CAP = 10


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
        except TimeoutError:
            pass


async def _cancel_watch_loop(
    task_id: uuid.UUID,
    stop: asyncio.Event,
    cancel_event: asyncio.Event,
) -> None:
    """Poll task.status every N seconds. Set cancel_event if it flips to 'canceled'.

    D31.b — responsive tear-down for cancel_task / cancel_investigation.
    """
    from sqlalchemy import text

    settings = get_settings()
    interval = max(1, settings.worker_cancel_poll_interval_s)
    while not stop.is_set():
        try:
            async with session_scope() as session:
                row = (
                    await session.execute(
                        text("SELECT status FROM tasks WHERE id = :id"),
                        {"id": task_id},
                    )
                ).first()
                if row is not None and row.status == "canceled":
                    log.info("worker.cancel_observed", task_id=str(task_id))
                    cancel_event.set()
                    stop.set()
                    return
        except Exception as e:
            log.warning("worker.cancel_watch_fail", task_id=str(task_id), error=str(e))
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


async def execute_task(task: Task, worker_id: str) -> None:
    """Run a single task to completion. Caller must have already claimed it.

    On rate-limit: records hit, requeues task (or dead-letters if bounce cap exceeded).
    On partial: marks partial.
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
    cancel_event = asyncio.Event()
    hb_task = asyncio.create_task(_heartbeat_loop(task.id, worker_id, stop))
    cw_task = asyncio.create_task(_cancel_watch_loop(task.id, stop, cancel_event))
    rate_limiter = RateLimitManager()

    result: HandlerResult | None = None
    handler_error: tuple[str, bool] | None = None  # (msg, is_timeout)
    canceled_observed = False

    try:
        # Open a session for the handler to share — lets handlers do task-adjacent writes
        # (investigation updates, signal records) in a single transaction. The outer
        # session (below) still handles the task lifecycle bookkeeping.
        async with session_scope() as handler_session:
            ctx = HandlerContext(
                task_id=str(task.id),
                task_type=task.type,
                payload=task.payload,
                vault_root=settings.vault_root,
                model=task.model,
                session=handler_session,
            )
            await emit_event(
                "dispatcher.worker",
                "task_start",
                {"task_id": str(task.id), "type": task.type, "worker_id": worker_id},
            )

            # D31.b: race handler against cancel_event. On cancel, propagate
            # CancelledError into the handler → CLIInvoker's finally kills subproc.
            handler_task = asyncio.create_task(handler(ctx))
            cancel_waiter = asyncio.create_task(cancel_event.wait())
            wall_timeout = max(60, settings.cli_wall_clock_timeout_s + 120)

            done, pending = await asyncio.wait(
                {handler_task, cancel_waiter},
                timeout=wall_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if cancel_waiter in done:
                handler_task.cancel()
                try:
                    await handler_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
                canceled_observed = True
            elif handler_task in done:
                cancel_waiter.cancel()
                try:
                    await cancel_waiter
                except asyncio.CancelledError:
                    pass
                result = handler_task.result()
            else:
                # timeout: both still pending
                handler_task.cancel()
                cancel_waiter.cancel()
                try:
                    await handler_task
                except (asyncio.CancelledError, Exception):
                    pass
                try:
                    await cancel_waiter
                except asyncio.CancelledError:
                    pass
                raise TimeoutError("handler wall-clock timeout")
    except TimeoutError:
        handler_error = ("handler wall-clock timeout", True)
    except Exception as e:
        handler_error = (
            f"{type(e).__name__}: {e}\n{traceback.format_exc()[:1500]}",
            False,
        )
    finally:
        stop.set()
        for t in (hb_task, cw_task):
            try:
                await t
            except Exception:
                pass

    if canceled_observed:
        await emit_event(
            "dispatcher.worker",
            "task_canceled_observed",
            {"task_id": str(task.id), "type": task.type, "worker_id": worker_id},
        )
        # DB already holds status='canceled'; don't overwrite via mark_*.
        return

    if handler_error is not None:
        async with session_scope() as session:
            await _handle_failure(session, task, handler_error[0])
        return

    assert result is not None
    llm: LLMResult | None = result.llm_result

    async with session_scope() as session:
        if llm is not None:
            await record_task_telemetry(session, task.id, llm)

        if result.message == "rate_limit" or (llm and llm.finish_reason == "rate_limit"):
            await rate_limiter.record_hit(session)
            if task.rate_limit_bounces + 1 >= RATE_LIMIT_BOUNCE_CAP:
                await mark_dead_letter(
                    session,
                    task.id,
                    f"rate-limit bounces exceeded cap ({RATE_LIMIT_BOUNCE_CAP})",
                )
                await emit_event(
                    "dispatcher.worker",
                    "task_dead_letter",
                    {
                        "task_id": str(task.id),
                        "type": task.type,
                        "reason": "rate_limit_bounce_cap",
                    },
                )
                return
            await requeue_on_rate_limit(session, task.id)
            await emit_event(
                "dispatcher.worker",
                "task_rate_limit",
                {
                    "task_id": str(task.id),
                    "type": task.type,
                    "bounces": task.rate_limit_bounces + 1,
                },
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
            if task.type == TaskType.RATE_LIMIT_PROBE.value:
                await rate_limiter.probe_succeeded(session)
            return

        validation = validator(task.payload, settings.vault_root)

        if validation.is_success:
            await mark_success(session, task.id, validation=validation)
            await emit_event(
                "dispatcher.worker",
                "task_success",
                {"task_id": str(task.id), "type": task.type, "ok": validation.ok},
            )
            await rate_limiter.reset_consecutive_hits(session)
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

        await _handle_failure(session, task, f"artifacts missing: {validation.missing}")


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
            {
                "task_id": str(task.id),
                "type": task.type,
                "attempts": task.attempts,
                "error": error[:500],
            },
        )
