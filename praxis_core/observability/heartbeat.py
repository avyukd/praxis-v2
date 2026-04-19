from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from praxis_core.db.models import Heartbeat
from praxis_core.db.session import session_scope
from praxis_core.logging import get_logger

log = get_logger("observability.heartbeat")


async def beat(component: str, status: dict[str, Any] | None = None) -> None:
    from praxis_core.time_et import now_utc

    async with session_scope() as session:
        stmt = insert(Heartbeat).values(
            component=component,
            last_heartbeat=now_utc(),
            status=status,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Heartbeat.component],
            set_={
                "last_heartbeat": stmt.excluded.last_heartbeat,
                "status": stmt.excluded.status,
            },
        )
        await session.execute(stmt)


async def heartbeat_loop(
    component: str,
    status_fn: Callable[[], dict[str, Any]] | None = None,
    interval_s: int = 30,
    stop_event: asyncio.Event | None = None,
) -> None:
    log.info("heartbeat.loop.start", component=component, interval_s=interval_s)
    while True:
        try:
            status = status_fn() if status_fn else None
            await beat(component, status)
        except Exception as e:
            log.warning("heartbeat.loop.error", component=component, error=str(e))
        if stop_event is not None and stop_event.is_set():
            log.info("heartbeat.loop.stop", component=component)
            return
        try:
            await asyncio.wait_for(
                stop_event.wait() if stop_event else _forever(), timeout=interval_s
            )
        except TimeoutError:
            continue


async def _forever() -> None:
    while True:
        await asyncio.sleep(3600)


async def stale_components(
    session: AsyncSession, stale_after_s: int = 120
) -> list[tuple[str, datetime, int]]:
    from praxis_core.time_et import now_utc

    cutoff = now_utc() - timedelta(seconds=stale_after_s)
    rows = (
        await session.execute(
            select(Heartbeat.component, Heartbeat.last_heartbeat).where(
                Heartbeat.last_heartbeat < cutoff
            )
        )
    ).all()
    out: list[tuple[str, datetime, int]] = []
    now = now_utc()
    for component, last in rows:
        staleness = int((now - last).total_seconds())
        out.append((component, last, staleness))
    return out
