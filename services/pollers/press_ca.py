"""Canadian press release poller — GNW CA + CNW + Newsfile with dedup + universe filter."""

from __future__ import annotations

import asyncio
import json
import signal
from enum import Enum

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from praxis_core.config import get_settings
from praxis_core.db.models import Source
from praxis_core.db.session import session_scope
from praxis_core.filters.market_cap import fetch_market_cap_usd, passes_mcap_filter
from praxis_core.logging import configure_logging, get_logger
from praxis_core.newswire.cnw import poll_cnw
from praxis_core.newswire.dedup import dedup_releases, fetch_release_text
from praxis_core.newswire.gnw import GNW_CA_FEEDS, poll_gnw
from praxis_core.newswire.models import PressRelease
from praxis_core.newswire.newsfile import poll_newsfile
from praxis_core.observability.events import emit_event
from praxis_core.observability.heartbeat import beat
from praxis_core.schemas.task_types import TaskType
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.time_et import et_iso
from praxis_core.vault import conventions as vc
from praxis_core.vault.writer import atomic_write

log = get_logger("pollers.press_ca")

# CA-specific mcap cap (D16). Starting at $2B USD equivalent; tune later.
CA_MARKET_CAP_MAX_USD = 2_000_000_000


class ReleaseResult(Enum):
    INGESTED = "ingested"
    REJECTED = "rejected"  # terminal; persisted in sources → cursor advances past
    TRANSIENT = "transient"  # retry next poll; cursor does NOT advance past


def _ca_yfinance_symbol(ticker: str, exchange: str) -> str:
    """Map ticker+exchange to yfinance symbol: TSX → TICKER.TO, TSXV → TICKER.V."""
    if exchange == "TSXV":
        return f"{ticker}.V"
    return f"{ticker}.TO"


async def _persist_rejection(
    release: PressRelease, reason: str, market_cap_usd: int | None = None
) -> None:
    """Write a 'rejected' source row so we don't re-evaluate this release every poll.

    Mirrors the EDGAR seen-set pattern: rejections are terminal, persisted
    in sources with source_type='press_release_rejected_<source>', allowing
    the cursor to advance past them safely.
    """
    dedup_key = f"pr:{release.source}:{release.release_id}"
    async with session_scope() as session:
        stmt = (
            insert(Source)
            .values(
                dedup_key=dedup_key,
                source_type=f"press_release_rejected_{release.source}",
                vault_path="",
                ticker=release.ticker or None,
                extra={
                    "release_id": release.release_id,
                    "ticker": release.ticker,
                    "exchange": release.exchange,
                    "title": release.title,
                    "url": release.url,
                    "source": release.source,
                    "reason": reason,
                    "market_cap_usd": market_cap_usd,
                    "rejected_at": et_iso(),
                },
            )
            .on_conflict_do_nothing(index_elements=[Source.dedup_key])
        )
        await session.execute(stmt)


async def _process_release(release: PressRelease) -> ReleaseResult:
    settings = get_settings()
    dedup_key = f"pr:{release.source}:{release.release_id}"

    # Seen-set: if already processed (ingested OR persisted rejection), skip.
    async with session_scope() as session:
        seen = (
            await session.execute(select(Source.id).where(Source.dedup_key == dedup_key))
        ).first()
    if seen is not None:
        return ReleaseResult.REJECTED

    if not release.ticker:
        await _persist_rejection(release, reason="no_ticker")
        await emit_event(
            "pollers.press_ca",
            "release_rejected",
            {"release_id": release.release_id, "source": release.source, "reason": "no_ticker"},
        )
        return ReleaseResult.REJECTED

    if release.exchange not in ("TSX", "TSXV"):
        await _persist_rejection(release, reason=f"exchange={release.exchange or 'unknown'}")
        await emit_event(
            "pollers.press_ca",
            "release_rejected",
            {
                "release_id": release.release_id,
                "source": release.source,
                "reason": f"exchange={release.exchange or 'unknown'}",
                "ticker": release.ticker,
            },
        )
        return ReleaseResult.REJECTED

    raw_dir = vc.raw_pr_dir(settings.vault_root, release.source, release.ticker, release.release_id)
    release_txt = raw_dir / "release.txt"
    index_json = raw_dir / "index.json"

    # CA universe filter — mcap via .TO/.V yfinance suffix
    yf_symbol = _ca_yfinance_symbol(release.ticker, release.exchange)
    async with session_scope() as session:
        lookup = await fetch_market_cap_usd(session, yf_symbol)
    mcap = lookup.market_cap_usd
    if not passes_mcap_filter(mcap, CA_MARKET_CAP_MAX_USD, keep_unknown=True):
        await _persist_rejection(
            release, reason=f"mcap ${mcap:,} > cap", market_cap_usd=mcap
        )
        await emit_event(
            "pollers.press_ca",
            "release_rejected",
            {
                "release_id": release.release_id,
                "ticker": release.ticker,
                "exchange": release.exchange,
                "reason": f"mcap ${mcap:,} > cap",
                "market_cap_usd": mcap,
            },
        )
        return ReleaseResult.REJECTED

    try:
        text = await fetch_release_text(release.url, release.source)
    except Exception as e:
        # Transient — don't advance cursor, will retry next poll.
        log.warning(
            "press_ca.fetch_fail",
            release_id=release.release_id,
            ticker=release.ticker,
            error=str(e),
        )
        return ReleaseResult.TRANSIENT

    if not text.strip():
        await _persist_rejection(release, reason="empty_body")
        return ReleaseResult.REJECTED

    atomic_write(release_txt, text)
    meta = {
        "release_id": release.release_id,
        "ticker": release.ticker,
        "exchange": release.exchange,
        "title": release.title,
        "url": release.url,
        "published_at": release.published_at,
        "source": release.source,
        "yf_symbol": yf_symbol,
        "ingested_at": et_iso(),
        "market_cap_usd": mcap,
    }
    atomic_write(index_json, json.dumps(meta, indent=2))

    rel_raw = str(release_txt.relative_to(settings.vault_root))
    async with session_scope() as session:
        stmt = (
            insert(Source)
            .values(
                dedup_key=dedup_key,
                source_type=f"press_release_{release.source}",
                vault_path=rel_raw,
                ticker=release.ticker,
                extra=meta,
            )
            .on_conflict_do_nothing(index_elements=[Source.dedup_key])
        )
        await session.execute(stmt)

        await enqueue_task(
            session,
            task_type=TaskType.ANALYZE_FILING,
            payload={
                "accession": release.release_id,
                "form_type": "press_release",
                "ticker": release.ticker,
                "cik": None,
                "raw_path": rel_raw,
                "source": release.source,
                "release_id": release.release_id,
            },
            priority=0,
            dedup_key=f"analyze_pr:{release.release_id}",
        )

    await emit_event(
        "pollers.press_ca",
        "release_ingested",
        {
            "release_id": release.release_id,
            "ticker": release.ticker,
            "exchange": release.exchange,
            "source": release.source,
            "market_cap_usd": mcap,
        },
    )
    log.info(
        "press_ca.ingested",
        release_id=release.release_id,
        ticker=release.ticker,
        exchange=release.exchange,
    )
    return ReleaseResult.INGESTED


async def poll_once() -> int:
    """Fetch all three CA newswires and run each candidate through
    _process_release. Dedup is authoritative via the seen-set lookup
    inside _process_release (sources.dedup_key). No cursor — polling
    the same 20-40 items per cycle is fine and avoids cursor-pollution
    bugs (CNW publishes pub_at as "14:30 ET" today but "Apr 18, 2026,
    02:04 ET" for older items, so lexicographic cursor comparison was
    silently skipping today's new releases whenever the cursor got
    advanced past a full-date entry)."""
    from_gnw = await poll_gnw(GNW_CA_FEEDS)
    from_newsfile = await poll_newsfile()
    from_cnw = await poll_cnw(pages=2)

    all_releases = list(from_gnw + from_newsfile + from_cnw)
    all_releases = dedup_releases(all_releases)

    ingested = 0
    for release in all_releases:
        result = await _process_release(release)
        if result is ReleaseResult.INGESTED:
            ingested += 1
    return ingested


async def run_loop(interval_s: int = 120) -> None:
    configure_logging()
    log.info("press_ca.start", interval_s=interval_s)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    while not stop_event.is_set():
        try:
            count = await poll_once()
            await beat(
                "pollers.press_ca",
                status={"last_poll_at": et_iso(), "ingested": count},
            )
        except Exception as e:
            log.exception("press_ca.loop_error", error=str(e))
            await beat(
                "pollers.press_ca",
                status={"last_poll_at": et_iso(), "error": str(e)[:200]},
            )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except TimeoutError:
            pass

    log.info("press_ca.shutdown")


def main() -> None:
    asyncio.run(run_loop())


if __name__ == "__main__":
    main()
