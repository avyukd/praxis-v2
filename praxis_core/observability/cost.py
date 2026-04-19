from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from praxis_core.db.models import Task
from praxis_core.llm.invoker import LLMResult


def build_telemetry(result: LLMResult) -> dict[str, Any]:
    return {
        "model": result.model,
        "invoker": result.invoker,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "cost_usd": result.cost_usd,
        "duration_s": result.duration_s,
        "finish_reason": result.finish_reason,
    }


async def record_task_telemetry(
    session: AsyncSession, task_id: uuid.UUID, result: LLMResult
) -> None:
    telemetry = build_telemetry(result)
    task = await session.get(Task, task_id)
    if task is None:
        return
    existing = dict(task.telemetry or {})
    existing.update(telemetry)
    task.telemetry = existing


async def today_cost_rollup(session: AsyncSession) -> dict[str, Any]:
    from sqlalchemy import Numeric, cast

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    stmt = (
        select(
            Task.type,
            func.count(Task.id).label("count"),
            func.coalesce(
                func.sum(cast(Task.telemetry["cost_usd"].astext, Numeric(12, 6))),
                0,
            ).label("cost_usd"),
            func.coalesce(
                func.sum(cast(Task.telemetry["tokens_in"].astext, Numeric(20))),
                0,
            ).label("tokens_in"),
            func.coalesce(
                func.sum(cast(Task.telemetry["tokens_out"].astext, Numeric(20))),
                0,
            ).label("tokens_out"),
        )
        .where(Task.finished_at >= today_start)
        .where(Task.telemetry.isnot(None))
        .group_by(Task.type)
    )
    rows = (await session.execute(stmt)).mappings().all()
    by_type: dict[str, dict[str, Any]] = {
        str(r["type"]): {
            "count": int(r["count"] or 0),
            "cost_usd": float(r["cost_usd"] or 0),
            "tokens_in": int(r["tokens_in"] or 0),
            "tokens_out": int(r["tokens_out"] or 0),
        }
        for r in rows
    }
    return {
        "by_type": by_type,
        "total_cost_usd": sum(r["cost_usd"] for r in by_type.values()),
        "total_tokens_in": sum(r["tokens_in"] for r in by_type.values()),
        "total_tokens_out": sum(r["tokens_out"] for r in by_type.values()),
    }
