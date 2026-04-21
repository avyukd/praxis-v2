"""US press release poller — polls GNW NYSE + NASDAQ RSS feeds."""

from __future__ import annotations

import argparse
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
from praxis_core.newswire.dedup import fetch_release_text
from praxis_core.newswire.gnw import GNW_US_FEEDS, poll_gnw
from praxis_core.newswire.models import PressRelease
from praxis_core.observability.events import emit_event
from praxis_core.observability.heartbeat import beat
from praxis_core.schemas.task_types import TaskType
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.time_et import et_iso
from praxis_core.vault import conventions as vc
from praxis_core.vault.writer import atomic_write

log = get_logger("pollers.press_us")


class ReleaseResult(Enum):
    INGESTED = "ingested"
    REJECTED = "rejected"  # terminal; persisted in sources → cursor advances past
    TRANSIENT = "transient"  # retry next poll; cursor does NOT advance past


async def _persist_rejection(
    release: PressRelease, reason: str, market_cap_usd: int | None = None
) -> None:
    dedup_key = f"pr:gnw:{release.release_id}"
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
    """Fetch release, apply filters, write to vault, enqueue analyze."""
    settings = get_settings()
    dedup_key = f"pr:gnw:{release.release_id}"

    # Seen-set: if already processed, skip.
    async with session_scope() as session:
        seen = (
            await session.execute(select(Source.id).where(Source.dedup_key == dedup_key))
        ).first()
    if seen is not None:
        return ReleaseResult.REJECTED

    if not release.ticker:
        await _persist_rejection(release, reason="no_ticker")
        return ReleaseResult.REJECTED

    raw_dir = vc.raw_pr_dir(settings.vault_root, release.source, release.ticker, release.release_id)
    release_txt = raw_dir / "release.txt"
    index_json = raw_dir / "index.json"

    async with session_scope() as session:
        lookup = await fetch_market_cap_usd(session, release.ticker)
    mcap = lookup.market_cap_usd
    if not passes_mcap_filter(mcap, settings.market_cap_max_usd, keep_unknown=True):
        await _persist_rejection(
            release, reason=f"mcap ${mcap:,} > cap", market_cap_usd=mcap
        )
        await emit_event(
            "pollers.press_us",
            "release_rejected",
            {
                "release_id": release.release_id,
                "ticker": release.ticker,
                "reason": f"mcap ${mcap:,} > cap",
                "market_cap_usd": mcap,
            },
        )
        return ReleaseResult.REJECTED

    try:
        text = await fetch_release_text(release.url, release.source)
    except Exception as e:
        log.warning(
            "press_us.fetch_fail",
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
        "pollers.press_us",
        "release_ingested",
        {
            "release_id": release.release_id,
            "ticker": release.ticker,
            "source": release.source,
            "market_cap_usd": mcap,
        },
    )
    log.info(
        "press_us.ingested",
        release_id=release.release_id,
        ticker=release.ticker,
    )
    return ReleaseResult.INGESTED


async def poll_once() -> int:
    """Fetch the NYSE + NASDAQ GNW feeds and run every release through
    _process_release. Dedup is authoritative via the seen-set check in
    _process_release (sources.dedup_key). No cursor — using a single
    `release_id` cursor across two feeds is fragile (if GNW ever
    assigns IDs out of strict global order, items with IDs below the
    current cursor get silently skipped forever), and the seen-set
    check is cheap (one Postgres SELECT per release)."""
    all_releases = await poll_gnw(GNW_US_FEEDS)

    ingested = 0
    for release in all_releases:
        result = await _process_release(release)
        if result is ReleaseResult.INGESTED:
            ingested += 1
    return ingested


async def run_loop(interval_s: int = 90) -> None:
    configure_logging()
    log.info("press_us.start", interval_s=interval_s)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    while not stop_event.is_set():
        try:
            count = await poll_once()
            await beat(
                "pollers.press_us",
                status={"last_poll_at": et_iso(), "ingested": count},
            )
        except Exception as e:
            log.exception("press_us.loop_error", error=str(e))
            await beat(
                "pollers.press_us",
                status={"last_poll_at": et_iso(), "error": str(e)[:200]},
            )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except TimeoutError:
            pass

    log.info("press_us.shutdown")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="poll once and exit (for smoke test)")
    args = parser.parse_args()
    if args.once:
        configure_logging()
        count = asyncio.run(poll_once())
        log.info("press_us.once", ingested=count)
        return
    asyncio.run(run_loop())


if __name__ == "__main__":
    main()
