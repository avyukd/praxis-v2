"""Investigation lifecycle helpers — touch_investigation for last_progress_at
maintenance (Section C D36)."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def touch_investigation(session: AsyncSession, investigation_id: uuid.UUID | None) -> None:
    """Bump Investigation.last_progress_at to now(). No-op if id is None."""
    if investigation_id is None:
        return
    await session.execute(
        text("UPDATE investigations SET last_progress_at = now() WHERE id = :id"),
        {"id": investigation_id},
    )
