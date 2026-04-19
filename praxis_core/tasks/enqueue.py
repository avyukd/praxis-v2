from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from praxis_core.db.models import Task
from praxis_core.schemas.payloads import validate_payload
from praxis_core.schemas.task_types import MODEL_TIERS, TASK_RESOURCE_KEYS, TaskModel, TaskType


def _resource_key_for(task_type: TaskType, payload: dict[str, Any]) -> str | None:
    kind = TASK_RESOURCE_KEYS.get(task_type)
    if kind is None:
        return None
    if kind == "company":
        ticker = payload.get("ticker")
        if not ticker:
            return None
        return f"company:{str(ticker).upper()}"
    if kind == "investigation":
        handle = payload.get("investigation_handle")
        if not handle:
            return None
        return f"investigation:{handle}"
    if kind in {"index", "lint", "journal"}:
        return f"{kind}:singleton"
    return None


async def enqueue_task(
    session: AsyncSession,
    *,
    task_type: TaskType | str,
    payload: dict[str, Any],
    priority: int,
    dedup_key: str | None = None,
    investigation_id: uuid.UUID | None = None,
    parent_task_id: uuid.UUID | None = None,
    depends_on: list[uuid.UUID] | None = None,
    model: TaskModel | None = None,
    max_attempts: int | None = None,
    resource_key_override: str | None = None,
) -> uuid.UUID | None:
    """Insert a task row, respecting dedup_key via ON CONFLICT DO NOTHING.

    Returns task id if inserted, None if dedup'd.
    """
    task_type = TaskType(task_type)
    validate_payload(task_type.value, payload)
    resolved_model = model or MODEL_TIERS[task_type]
    resource_key = resource_key_override or _resource_key_for(task_type, payload)

    stmt = insert(Task).values(
        type=task_type.value,
        priority=priority,
        status="queued",
        model=resolved_model.value,
        payload=payload,
        dedup_key=dedup_key,
        resource_key=resource_key,
        investigation_id=investigation_id,
        parent_task_id=parent_task_id,
        depends_on=depends_on,
        attempts=0,
        rate_limit_bounces=0,
        max_attempts=max_attempts if max_attempts is not None else 3,
    )
    if dedup_key is not None:
        stmt = stmt.on_conflict_do_nothing(index_elements=[Task.dedup_key])
    stmt = stmt.returning(Task.id)
    result = await session.execute(stmt)
    row = result.first()
    return row.id if row else None
