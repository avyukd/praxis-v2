"""Postgres cache for fundamentals MCP (D25).

Keyed by (ticker, method, params_hash). TTL enforced at read time by
comparing `fetched_at` to `now() - ttl`. Default TTL: 1 hour.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from praxis_core.db.session import session_scope
from praxis_core.logging import get_logger

log = get_logger("mcp.fundamentals.cache")

DEFAULT_TTL = timedelta(hours=1)


def params_hash(params: dict[str, Any]) -> str:
    canonical = json.dumps(params, sort_keys=True, default=str)
    return hashlib.md5(canonical.encode("utf-8")).hexdigest()[:32]


async def cache_get(
    session: AsyncSession,
    ticker: str,
    method: str,
    params: dict[str, Any],
    ttl: timedelta = DEFAULT_TTL,
) -> dict[str, Any] | None:
    ph = params_hash(params)
    result = await session.execute(
        text(
            """
            SELECT value FROM fundamentals_cache
            WHERE ticker = :t AND method = :m AND params_hash = :ph
              AND fetched_at > now() - :ttl * interval '1 second'
              AND value IS NOT NULL
            """
        ),
        {"t": ticker.upper(), "m": method, "ph": ph, "ttl": int(ttl.total_seconds())},
    )
    row = result.first()
    return row.value if row else None


async def cache_set(
    session: AsyncSession,
    ticker: str,
    method: str,
    params: dict[str, Any],
    value: dict[str, Any] | list[Any],
) -> None:
    ph = params_hash(params)
    await session.execute(
        text(
            """
            INSERT INTO fundamentals_cache (ticker, method, params_hash, value, fetched_at)
            VALUES (:t, :m, :ph, CAST(:v AS jsonb), now())
            ON CONFLICT (ticker, method, params_hash)
            DO UPDATE SET value = EXCLUDED.value, fetched_at = now(), last_error = NULL
            """
        ),
        {"t": ticker.upper(), "m": method, "ph": ph, "v": json.dumps(value, default=str)},
    )


async def cache_mark_error(
    session: AsyncSession, ticker: str, method: str, params: dict[str, Any], error: str
) -> None:
    ph = params_hash(params)
    await session.execute(
        text(
            """
            INSERT INTO fundamentals_cache (ticker, method, params_hash, value, fetched_at, last_error)
            VALUES (:t, :m, :ph, NULL, now(), :err)
            ON CONFLICT (ticker, method, params_hash)
            DO UPDATE SET fetched_at = now(), last_error = EXCLUDED.last_error
            """
        ),
        {"t": ticker.upper(), "m": method, "ph": ph, "err": error[:500]},
    )


async def with_cache(
    ticker: str,
    method: str,
    params: dict[str, Any],
    fetch_fn,
    ttl: timedelta = DEFAULT_TTL,
) -> dict[str, Any] | list[Any]:
    """Read-through cache. fetch_fn is a sync callable returning the value."""
    import asyncio

    async with session_scope() as session:
        cached = await cache_get(session, ticker, method, params, ttl)
        if cached is not None:
            return cached

    try:
        value = await asyncio.to_thread(fetch_fn)
    except Exception as e:
        async with session_scope() as session:
            await cache_mark_error(session, ticker, method, params, str(e))
        raise

    async with session_scope() as session:
        await cache_set(session, ticker, method, params, value)
    return value
