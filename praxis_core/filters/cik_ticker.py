"""CIK → ticker mapping via SEC's canonical company_tickers.json.

Endpoint: https://www.sec.gov/files/company_tickers.json
Format:
    {
      "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
      "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
      ...
    }

We cache this in Postgres `system_state` under key 'cik_ticker_map' as a single JSON blob
plus a fetched_at timestamp. Refreshed daily. ~10k entries, ~500KB.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, stop_after_attempt, wait_exponential

from praxis_core.config import get_settings
from praxis_core.logging import get_logger
from praxis_core.time_et import now_utc

log = get_logger("filters.cik_ticker")

SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SYSTEM_KEY = "cik_ticker_map"


@dataclass
class CikTickerMap:
    cik_to_ticker: dict[str, str]  # "0000320193" -> "AAPL"
    fetched_at: datetime

    def lookup(self, cik: str) -> str | None:
        """Normalize CIK to 10-digit zero-padded and look up."""
        return self.cik_to_ticker.get(str(cik).strip().zfill(10))


@retry(wait=wait_exponential(multiplier=1, min=2, max=30), stop=stop_after_attempt(4))
async def _fetch_sec_map(user_agent: str) -> dict[str, str]:
    async with httpx.AsyncClient(
        timeout=30.0,
        headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
    ) as client:
        r = await client.get(SEC_COMPANY_TICKERS_URL)
        r.raise_for_status()
        raw: dict[str, Any] = r.json()
    out: dict[str, str] = {}
    for _idx, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        cik = entry.get("cik_str")
        ticker = entry.get("ticker")
        if cik is None or not ticker:
            continue
        out[str(int(cik)).zfill(10)] = str(ticker).upper()
    return out


async def load_cik_ticker_map(
    session: AsyncSession,
    *,
    force_refresh: bool = False,
) -> CikTickerMap:
    """Load the CIK↔ticker map from cache, refreshing if stale or forced."""
    settings = get_settings()
    now = now_utc()
    ttl = timedelta(seconds=settings.cik_ticker_refresh_interval_s)

    if not force_refresh:
        row = (
            await session.execute(
                text("SELECT value, updated_at FROM system_state WHERE key = :k"),
                {"k": _SYSTEM_KEY},
            )
        ).first()
        if row is not None:
            value = row.value if isinstance(row.value, dict) else json.loads(row.value)
            fetched_raw = value.get("fetched_at")
            if isinstance(fetched_raw, str):
                fetched_at = datetime.fromisoformat(fetched_raw)
                if now - fetched_at < ttl:
                    return CikTickerMap(
                        cik_to_ticker=value.get("map", {}),
                        fetched_at=fetched_at,
                    )

    log.info("cik_ticker.fetching_sec_map")
    try:
        mapping = await _fetch_sec_map(settings.sec_user_agent)
    except Exception as e:
        log.warning("cik_ticker.fetch_failed", error=str(e))
        # Fall back to cached value even if stale
        row = (
            await session.execute(
                text("SELECT value FROM system_state WHERE key = :k"),
                {"k": _SYSTEM_KEY},
            )
        ).first()
        if row is not None:
            value = row.value if isinstance(row.value, dict) else json.loads(row.value)
            fallback_at = value.get("fetched_at") or now.isoformat()
            if not isinstance(fallback_at, str):
                fallback_at = now.isoformat()
            return CikTickerMap(
                cik_to_ticker=value.get("map", {}),
                fetched_at=datetime.fromisoformat(fallback_at),
            )
        raise

    payload = {"map": mapping, "fetched_at": now.isoformat()}
    await session.execute(
        text(
            """
            INSERT INTO system_state (key, value, updated_at)
            VALUES (:k, CAST(:v AS jsonb), now())
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
            """
        ),
        {"k": _SYSTEM_KEY, "v": json.dumps(payload)},
    )
    log.info("cik_ticker.cached", entry_count=len(mapping))
    return CikTickerMap(cik_to_ticker=mapping, fetched_at=now)
