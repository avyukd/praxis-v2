from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from praxis_core.db.models import Event
from praxis_core.db.session import session_scope


async def emit_event(
    component: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    async with session_scope() as session:
        session.add(
            Event(
                component=component,
                event_type=event_type,
                payload=payload,
            )
        )


async def recent_events(
    session: AsyncSession, limit: int = 50, component: str | None = None
) -> list[Event]:
    stmt = select(Event).order_by(Event.ts.desc()).limit(limit)
    if component is not None:
        stmt = stmt.where(Event.component == component)
    return list((await session.execute(stmt)).scalars().all())
