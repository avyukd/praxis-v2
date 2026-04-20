"""Market-cap lookup via yfinance with Postgres cache.

yfinance is sync; we wrap lookups in asyncio.to_thread so callers can `await`.
Cache: `market_cap_cache` table keyed by ticker. TTL configurable (default 24h).
On cache miss or stale, we fetch; on fetch failure, we keep the stale value but log
an event so observability surfaces the degradation.

Semantics for the filter gate:
  - Known + <= max_usd  → pass
  - Known + > max_usd   → drop
  - Unknown (None)      → pass by default (preserves small/obscure micro-cap coverage)
                          Can be flipped to drop-on-unknown via keep_unknown=False.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from praxis_core.config import get_settings
from praxis_core.logging import get_logger
from praxis_core.time_et import now_utc

log = get_logger("filters.market_cap")


@dataclass
class MarketCapLookup:
    ticker: str
    market_cap_usd: int | None
    source: str
    fetched_at: datetime
    from_cache: bool


def _yfinance_fetch(ticker: str) -> tuple[int | None, str | None]:
    """Synchronous yfinance call. Returns (mcap, error_message)."""
    try:
        import yfinance  # noqa: PLC0415 — heavy import, keep lazy
    except ImportError:
        return None, "yfinance not installed"
    try:
        t = yfinance.Ticker(ticker)
        info = t.info or {}
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"[:200]
    mcap = info.get("marketCap")
    if mcap is None:
        return None, "no marketCap in yfinance info"
    try:
        mcap_int = int(mcap)
    except (TypeError, ValueError):
        return None, f"non-numeric marketCap: {mcap!r}"
    if mcap_int <= 0:
        return None, "marketCap is zero or negative"
    return mcap_int, None


async def fetch_market_cap_usd(
    session: AsyncSession,
    ticker: str,
    *,
    force_refresh: bool = False,
) -> MarketCapLookup:
    """Look up a ticker's market cap. Hits cache first, then yfinance.

    Always returns a MarketCapLookup — market_cap_usd may be None if unknown.
    """
    settings = get_settings()
    ticker = ticker.strip().upper()
    now = now_utc()
    ttl = timedelta(seconds=settings.market_cap_cache_ttl_s)

    if not force_refresh:
        row = (
            await session.execute(
                text(
                    "SELECT ticker, market_cap_usd, source, fetched_at FROM market_cap_cache "
                    "WHERE ticker = :t"
                ),
                {"t": ticker},
            )
        ).first()
        if row is not None and now - row.fetched_at < ttl:
            return MarketCapLookup(
                ticker=row.ticker,
                market_cap_usd=row.market_cap_usd,
                source=row.source,
                fetched_at=row.fetched_at,
                from_cache=True,
            )

    mcap, err = await asyncio.to_thread(_yfinance_fetch, ticker)
    await session.execute(
        text(
            """
            INSERT INTO market_cap_cache (ticker, market_cap_usd, source, fetched_at, last_error)
            VALUES (:t, :m, 'yfinance', now(), :e)
            ON CONFLICT (ticker) DO UPDATE
              SET market_cap_usd = EXCLUDED.market_cap_usd,
                  source = EXCLUDED.source,
                  fetched_at = EXCLUDED.fetched_at,
                  last_error = EXCLUDED.last_error
            """
        ),
        {"t": ticker, "m": mcap, "e": err},
    )

    if err:
        log.debug("market_cap.fetch_failed", ticker=ticker, error=err)

    return MarketCapLookup(
        ticker=ticker,
        market_cap_usd=mcap,
        source="yfinance",
        fetched_at=now,
        from_cache=False,
    )


async def get_cached_mcap(session: AsyncSession, ticker: str) -> int | None:
    """Cache-only lookup. Returns None if not cached. No yfinance call.

    Used by handlers that want whatever mcap the pollers already warmed
    without triggering new yfinance traffic per filing.
    """
    ticker = ticker.strip().upper()
    row = (
        await session.execute(
            text("SELECT market_cap_usd FROM market_cap_cache WHERE ticker = :t"),
            {"t": ticker},
        )
    ).first()
    return row.market_cap_usd if row is not None else None


def passes_mcap_filter(
    mcap: int | None,
    max_usd: int,
    *,
    keep_unknown: bool = True,
) -> bool:
    """Decide whether a filing with the given mcap passes the filter.

    Policy: keep if unknown (small-cap obscure names worth seeing); drop if above cap.
    """
    if mcap is None:
        return keep_unknown
    return mcap <= max_usd
