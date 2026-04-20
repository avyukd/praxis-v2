"""Poller state persistence in system_state table."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_state(session: AsyncSession, key: str) -> dict[str, Any]:
    row = (
        await session.execute(
            text("SELECT value FROM system_state WHERE key = :k"),
            {"k": key},
        )
    ).first()
    if row is None:
        return {}
    return dict(row.value or {})


async def set_state(session: AsyncSession, key: str, value: dict[str, Any]) -> None:
    await session.execute(
        text(
            """
            INSERT INTO system_state (key, value, updated_at)
            VALUES (:k, CAST(:v AS jsonb), now())
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value,
                  updated_at = EXCLUDED.updated_at
            """
        ),
        {"k": key, "v": __import__("json").dumps(value)},
    )
