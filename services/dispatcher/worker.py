from __future__ import annotations

import asyncio
import json
import traceback
import uuid
from collections.abc import Coroutine
from typing import cast

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
    release_task,
    requeue_on_rate_limit,
)
from praxis_core.tasks.validators import get_validator
from services.dispatcher.investability import handle_post_dive_investability

log = get_logger("dispatcher.worker")

# If a task has bounced on rate limits this many times, dead-letter it.
RATE_LIMIT_BOUNCE_CAP = 10
INTERRUPTED_TASK_BACKOFF_S = 5


def validation_failure_reason(validation) -> str:
    if validation.malformed:
        reasons = [f"{item.path}: {item.reason}" for item in validation.malformed]
        return "artifacts malformed: " + "; ".join(reasons)
    if validation.missing:
        return f"artifacts missing: {validation.missing}"
    return "validation failed with no details"


async def requeue_canceled_task(task: Task, *, backoff_s: int = 5) -> None:
    """Return shutdown-canceled work to the queue instead of leaving it running."""
    async with session_scope() as session:
        await release_task(session, task.id, backoff_s=backoff_s)
        await emit_event(
            "dispatcher.worker",
            "task_shutdown_requeued",
            {"task_id": str(task.id), "type": task.type, "backoff_s": backoff_s},
            session=session,
        )
    log.warning("task.shutdown_requeued", task_id=str(task.id), task_type=task.type)


async def requeue_interrupted_llm_task(
    session,
    task: Task,
    llm: LLMResult | None,
    *,
    backoff_s: int = INTERRUPTED_TASK_BACKOFF_S,
) -> bool:
    if llm is None or llm.finish_reason != "killed":
        return False
    await release_task(session, task.id, backoff_s=backoff_s)
    await emit_event(
        "dispatcher.worker",
        "task_interrupted_requeued",
        {
            "task_id": str(task.id),
            "type": task.type,
            "backoff_s": backoff_s,
        },
        session=session,
    )
    log.warning("task.interrupted_requeued", task_id=str(task.id), task_type=task.type)
    return True


def retry_payload_patch(task: Task, error: str) -> dict[str, object] | None:
    """Attach targeted repair guidance to retried artifact-producing tasks."""
    if not task.type.startswith("dive_"):
        return None
    if not (
        error.startswith("artifacts malformed:") or error.startswith("artifacts missing:")
    ):
        return None
    prior_count = 0
    if isinstance(task.payload, dict):
        try:
            prior_count = int(task.payload.get("_retry_count", 0))
        except (TypeError, ValueError):
            prior_count = 0
    return {
        "_retry_reason": error[:1500],
        "_retry_count": prior_count + 1,
    }


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
    try:
        settings = get_settings()
        registry = get_handler_registry()
        handler = registry.get(task.type)
        if handler is None:
            async with session_scope() as session:
                await mark_failed(
                    session, task.id, f"no handler registered for task type {task.type}"
                )
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
            # Handlers get no shared session. A prior design passed a
            # long-lived session so "task-adjacent writes" could be atomic
            # with task lifecycle. In practice handlers do one read at the
            # top (e.g. SELECT investigations.initiated_by) then run an LLM
            # call for 15+ min. The read opened an asyncpg transaction that
            # sat 'idle in transaction' for the full LLM duration, starving
            # the pg connection pool and hanging the dispatcher.
            # Every handler using ctx.session has a graceful `if is None:
            # async with session_scope()` fallback — they now hit that path
            # and commit after each logical unit of work.
            ctx = HandlerContext(
                task_id=str(task.id),
                task_type=task.type,
                payload=task.payload,
                vault_root=settings.vault_root,
                model=task.model,
                session=None,
            )
            await emit_event(
                "dispatcher.worker",
                "task_start",
                {"task_id": str(task.id), "type": task.type, "worker_id": worker_id},
            )

            # D31.b: race handler against cancel_event. On cancel, propagate
            # CancelledError into the handler → CLIInvoker's finally kills subproc.
            handler_coro = cast(Coroutine[object, object, HandlerResult], handler(ctx))
            handler_task = asyncio.create_task(handler_coro)
            cancel_waiter = asyncio.create_task(cancel_event.wait())
            # Worker-level wall timeout sits ABOVE the CLI invoker's own
            # timeout so the invoker's SIGTERM grace window (60s) + SIGKILL
            # fallback can all run before the worker gives up on the task.
            # CLI 3600s + 300s buffer = 3900s (65 min) at default config.
            wall_timeout = max(60, settings.cli_wall_clock_timeout_s + 300)

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

            if await requeue_interrupted_llm_task(session, task, llm):
                return

            if result.message == "rate_limit" or (llm and llm.finish_reason == "rate_limit"):
                upstream_resets_at = llm.rate_limit_resets_at if llm is not None else None
                await rate_limiter.record_hit(session, upstream_resets_at=upstream_resets_at)

                # Probe tasks are single-use signals. record_hit above already
                # updated rate_limit_state; requeueing the probe would accumulate
                # stale probes (seen 7 queued at once). Mark failed and let the
                # next _maybe_launch_probe cycle spawn a fresh one.
                if task.type == TaskType.RATE_LIMIT_PROBE.value:
                    await mark_failed(session, task.id, "probe observed upstream rate_limit")
                    await emit_event(
                        "dispatcher.worker",
                        "task_rate_limit",
                        {
                            "task_id": str(task.id),
                            "type": task.type,
                            "probe_single_use": True,
                        },
                        session=session,
                    )
                    return

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
                        session=session,
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
                    session=session,
                )
                return

            if not result.ok:
                if result.transient:
                    # Cooperative "not ready, retry later" — e.g. synthesize_memo
                    # waiting for parallel dives to finish. Release the lease and
                    # put the task back in the queue WITHOUT incrementing attempts
                    # so it doesn't burn max_attempts on legitimate async gating.
                    await release_task(session, task.id)
                    await emit_event(
                        "dispatcher.worker",
                        "task_transient_retry",
                        {
                            "task_id": str(task.id),
                            "type": task.type,
                            "reason": result.message or "transient",
                        },
                        session=session,
                    )
                    log.info(
                        "task.transient_retry",
                        task_id=str(task.id),
                        task_type=task.type,
                        reason=(result.message or "transient")[:200],
                    )
                    return
                await _handle_failure(session, task, result.message or "handler returned ok=False")
                return

            validator = get_validator(task.type)
            if validator is None:
                await mark_success(session, task.id)
                await emit_event(
                    "dispatcher.worker",
                    "task_success",
                    {"task_id": str(task.id), "type": task.type, "validation": "skipped"},
                    session=session,
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
                    session=session,
                )
                await rate_limiter.reset_consecutive_hits(session)
                if task.type == TaskType.RATE_LIMIT_PROBE.value:
                    await rate_limiter.probe_succeeded(session)
                if task.type == TaskType.DIVE_FINANCIAL_RIGOROUS.value:
                    try:
                        await handle_post_dive_investability(
                            session, task, settings.vault_root
                        )
                    except Exception as e:
                        log.warning(
                            "investability.hook_fail", task_id=str(task.id), error=str(e)
                        )
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
                    session=session,
                )
                return

            await _handle_failure(session, task, validation_failure_reason(validation))
    except asyncio.CancelledError:
        await asyncio.shield(requeue_canceled_task(task))
        raise


async def _handle_failure(session, task: Task, error: str) -> None:
    if task.attempts >= task.max_attempts:
        await mark_dead_letter(session, task.id, error)
        await emit_event(
            "dispatcher.worker",
            "task_dead_letter",
            {"task_id": str(task.id), "type": task.type, "error": error[:500]},
            session=session,
        )
    else:
        await mark_failed(session, task.id, error)
        from sqlalchemy import text

        payload_patch = retry_payload_patch(task, error)
        await session.execute(
            text(
                "UPDATE tasks SET status = 'queued', lease_holder = NULL, "
                "lease_expires_at = NULL, last_error = :err, "
                "payload = CASE WHEN :payload_patch IS NULL THEN payload "
                "ELSE COALESCE(payload, '{}'::jsonb) || CAST(:payload_patch AS jsonb) END "
                "WHERE id = :tid"
            ),
            {
                "err": error[:2000],
                "tid": task.id,
                "payload_patch": json.dumps(payload_patch) if payload_patch is not None else None,
            },
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
            session=session,
        )
