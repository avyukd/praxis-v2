from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from praxis_core.config import get_settings
from praxis_core.db.models import DeadLetterTask, Task
from praxis_core.logging import get_logger
from praxis_core.schemas.artifacts import ValidationResult

log = get_logger("tasks.lifecycle")


async def claim_next_task(
    session: AsyncSession,
    *,
    worker_id: str,
    allowed_types: list[str] | None = None,
    excluded_models: list[str] | None = None,
    excluded_resource_keys: list[str] | None = None,
) -> Task | None:
    """Atomically claim the next eligible task.

    Rules:
      - status IN ('queued', 'partial')
      - lease not held (or lease expired)
      - resource_key not in excluded_resource_keys (currently-running resources)
      - priority order: ascending (0 highest)
      - Within priority: oldest first, but tasks older than age_bump_after_min
        get effectively promoted one tier via the ORDER BY expression
      - Uses FOR UPDATE SKIP LOCKED to avoid two dispatchers stomping each other
    """
    settings = get_settings()
    age_bump_seconds = settings.age_bump_after_min * 60

    params: dict[str, Any] = {
        "worker_id": worker_id,
        "lease_s": settings.worker_lease_s,
        "age_bump_seconds": age_bump_seconds,
    }

    type_clause = ""
    if allowed_types:
        type_clause = "AND type = ANY(:allowed_types)"
        params["allowed_types"] = allowed_types

    model_clause = ""
    if excluded_models:
        model_clause = "AND model != ALL(:excluded_models)"
        params["excluded_models"] = excluded_models

    resource_clause = ""
    if excluded_resource_keys:
        resource_clause = (
            "AND (resource_key IS NULL OR resource_key != ALL(:excluded_resource_keys))"
        )
        params["excluded_resource_keys"] = excluded_resource_keys

    # Claim queued/partial, OR running tasks whose lease has expired (crash recovery).
    sql = f"""
        WITH candidate AS (
          SELECT id
          FROM tasks
          WHERE (
              (status IN ('queued', 'partial')
               AND (lease_expires_at IS NULL OR lease_expires_at < now()))
              OR
              (status = 'running'
               AND lease_expires_at IS NOT NULL
               AND lease_expires_at < now())
            )
            {type_clause}
            {model_clause}
            {resource_clause}
          ORDER BY
            priority - (CASE WHEN EXTRACT(EPOCH FROM now() - created_at) > :age_bump_seconds THEN 1 ELSE 0 END) ASC,
            created_at ASC
          LIMIT 1
          FOR UPDATE SKIP LOCKED
        )
        UPDATE tasks SET
          status = 'running',
          lease_holder = :worker_id,
          lease_expires_at = now() + :lease_s * interval '1 second',
          attempts = attempts + 1,
          started_at = COALESCE(started_at, now())
        WHERE id = (SELECT id FROM candidate)
        RETURNING id
    """
    result = await session.execute(text(sql), params)
    row = result.first()
    if row is None:
        return None
    task = await session.get(Task, row.id)
    if task is not None:
        # UPDATE bypassed the ORM so identity-map may hold a stale copy.
        await session.refresh(task)
        log.info(
            "task.claimed",
            task_id=str(task.id),
            task_type=task.type,
            worker_id=worker_id,
            attempts=task.attempts,
        )
    return task


async def extend_lease(session: AsyncSession, task_id: uuid.UUID, worker_id: str) -> bool:
    settings = get_settings()
    result = await session.execute(
        text(
            """
            UPDATE tasks
            SET lease_expires_at = now() + :lease_s * interval '1 second'
            WHERE id = :task_id AND lease_holder = :worker_id AND status = 'running'
            RETURNING id
            """
        ),
        {"task_id": task_id, "worker_id": worker_id, "lease_s": settings.worker_lease_s},
    )
    return result.first() is not None


async def mark_running(session: AsyncSession, task_id: uuid.UUID, worker_id: str) -> None:
    """Idempotent — used when the worker wants to mark its newly-claimed task running."""
    settings = get_settings()
    await session.execute(
        text(
            """
            UPDATE tasks
            SET status = 'running',
                lease_holder = :worker_id,
                lease_expires_at = now() + :lease_s * interval '1 second',
                started_at = COALESCE(started_at, now())
            WHERE id = :task_id
            """
        ),
        {"task_id": task_id, "worker_id": worker_id, "lease_s": settings.worker_lease_s},
    )


async def mark_success(
    session: AsyncSession,
    task_id: uuid.UUID,
    validation: ValidationResult | None = None,
    telemetry: dict[str, Any] | None = None,
) -> None:
    """Transition task to success. No-op if task is already in a terminal state
    (e.g., canceled via MCP while the worker was still running — D31.a guard)."""
    v = validation.model_dump() if validation else None
    await session.execute(
        text(
            """
            UPDATE tasks
            SET status = 'success',
                finished_at = now(),
                lease_holder = NULL,
                lease_expires_at = NULL,
                validation_result = :validation,
                telemetry = COALESCE(telemetry, '{}'::jsonb) || COALESCE(CAST(:telemetry AS jsonb), '{}'::jsonb)
            WHERE id = :task_id AND status = 'running'
            """
        ),
        {"task_id": task_id, "validation": _jsonb(v), "telemetry": _jsonb(telemetry)},
    )
    log.info("task.success", task_id=str(task_id))


async def mark_partial(
    session: AsyncSession,
    task_id: uuid.UUID,
    validation: ValidationResult,
    telemetry: dict[str, Any] | None = None,
) -> None:
    """No-op if task is already terminal (D31.a guard)."""
    await session.execute(
        text(
            """
            UPDATE tasks
            SET status = 'partial',
                finished_at = now(),
                lease_holder = NULL,
                lease_expires_at = NULL,
                validation_result = :validation,
                telemetry = COALESCE(telemetry, '{}'::jsonb) || COALESCE(CAST(:telemetry AS jsonb), '{}'::jsonb)
            WHERE id = :task_id AND status = 'running'
            """
        ),
        {
            "task_id": task_id,
            "validation": _jsonb(validation.model_dump()),
            "telemetry": _jsonb(telemetry),
        },
    )
    log.info("task.partial", task_id=str(task_id), validation=validation.model_dump())


async def mark_failed(
    session: AsyncSession,
    task_id: uuid.UUID,
    error: str,
    telemetry: dict[str, Any] | None = None,
) -> None:
    """No-op if task is already terminal (D31.a guard)."""
    await session.execute(
        text(
            """
            UPDATE tasks
            SET status = 'failed',
                lease_holder = NULL,
                lease_expires_at = NULL,
                last_error = :error,
                telemetry = COALESCE(telemetry, '{}'::jsonb) || COALESCE(CAST(:telemetry AS jsonb), '{}'::jsonb)
            WHERE id = :task_id AND status = 'running'
            """
        ),
        {"task_id": task_id, "error": error[:2000], "telemetry": _jsonb(telemetry)},
    )
    log.warning("task.failed", task_id=str(task_id), error=error[:200])


async def mark_dead_letter(session: AsyncSession, task_id: uuid.UUID, final_error: str) -> None:
    task = await session.get(Task, task_id)
    if task is None:
        return
    original = {
        "id": str(task.id),
        "type": task.type,
        "priority": task.priority,
        "payload": task.payload,
        "attempts": task.attempts,
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }
    session.add(DeadLetterTask(id=task.id, original_task=original, final_error=final_error[:2000]))
    await session.execute(
        text(
            """
            UPDATE tasks
            SET status = 'dead_letter',
                finished_at = now(),
                lease_holder = NULL,
                lease_expires_at = NULL,
                last_error = :error
            WHERE id = :task_id AND status IN ('running', 'failed')
            """
        ),
        {"task_id": task_id, "error": final_error[:2000]},
    )
    log.error("task.dead_letter", task_id=str(task_id), error=final_error[:200])


async def requeue_on_rate_limit(session: AsyncSession, task_id: uuid.UUID) -> None:
    await session.execute(
        text(
            """
            UPDATE tasks
            SET status = 'queued',
                lease_holder = NULL,
                lease_expires_at = NULL,
                rate_limit_bounces = rate_limit_bounces + 1,
                attempts = GREATEST(0, attempts - 1)
            WHERE id = :task_id
            """
        ),
        {"task_id": task_id},
    )
    log.info("task.requeued.rate_limit", task_id=str(task_id))


async def release_task(
    session: AsyncSession, task_id: uuid.UUID, backoff_s: int = 30
) -> None:
    """Cooperative release — put back to queued without consuming an attempt.

    Decrements attempts because claim_next_task unconditionally increments
    on every claim. Without the decrement, a task that transient-retries
    N times ends up with attempts=N+1, visible as "N retries" and burning
    through max_attempts on legitimate cooperative waits.

    Sets lease_expires_at = now() + backoff_s with lease_holder=NULL so the
    claim query (which skips tasks whose lease_expires_at is in the future)
    won't immediately re-grab it. Prevents the 2s-tick spin where a
    synthesize_memo waiting on a 5-min dive burns 150 claims + handler runs.
    """
    await session.execute(
        text(
            """
            UPDATE tasks
            SET status = 'queued',
                lease_holder = NULL,
                lease_expires_at = now() + :backoff_s * interval '1 second',
                attempts = GREATEST(0, attempts - 1)
            WHERE id = :task_id AND status = 'running'
            """
        ),
        {"task_id": task_id, "backoff_s": backoff_s},
    )


def _jsonb(v: Any) -> str | None:
    if v is None:
        return None
    import json

    return json.dumps(v)
