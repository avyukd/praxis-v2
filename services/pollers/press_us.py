"""US press release poller — polls GNW NYSE + NASDAQ RSS feeds."""

from __future__ import annotations

import asyncio
import hashlib
import json
import signal

from sqlalchemy.dialects.postgresql import insert

from praxis_core.config import get_settings
from praxis_core.db.models import Source
from praxis_core.db.session import session_scope
from praxis_core.filters.market_cap import fetch_market_cap_usd, passes_mcap_filter
from praxis_core.logging import configure_logging, get_logger
from praxis_core.newswire.dedup import fetch_release_text
from praxis_core.newswire.gnw import GNW_US_FEEDS, poll_gnw
from praxis_core.newswire.models import PressRelease
from praxis_core.newswire.state import get_state, set_state
from praxis_core.observability.events import emit_event
from praxis_core.observability.heartbeat import beat
from praxis_core.schemas.task_types import TaskType
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.time_et import et_iso
from praxis_core.vault import conventions as vc
from praxis_core.vault.writer import atomic_write

log = get_logger("pollers.press_us")

STATE_KEY = "poller_state.press_us.last_seen"


async def _process_release(release: PressRelease) -> bool:
    """Fetch release, apply filters, write to vault, enqueue analyze. Returns True if ingested."""
    settings = get_settings()
    if not release.ticker:
        return False

    dedup_key = f"pr:gnw:{release.release_id}"
    raw_dir = vc.raw_pr_dir(settings.vault_root, release.source, release.ticker, release.release_id)
    release_txt = raw_dir / "release.txt"
    index_json = raw_dir / "index.json"
    if release_txt.exists() and index_json.exists():
        return False

    async with session_scope() as session:
        lookup = await fetch_market_cap_usd(session, release.ticker)
    mcap = lookup.market_cap_usd
    if not passes_mcap_filter(mcap, settings.market_cap_max_usd, keep_unknown=True):
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
        return False

    try:
        text = await fetch_release_text(release.url, release.source)
    except Exception as e:
        log.warning(
            "press_us.fetch_fail",
            release_id=release.release_id,
            ticker=release.ticker,
            error=str(e),
        )
        return False

    if not text.strip():
        return False

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
    return True


def _dedup_content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


async def poll_once() -> int:
    async with session_scope() as session:
        state = await get_state(session, STATE_KEY)
    last_seen: dict[str, str] = dict(state.get("last_seen", {}))

    all_releases = await poll_gnw(GNW_US_FEEDS)

    new_releases: list[PressRelease] = []
    newest = dict(last_seen)
    for r in all_releases:
        src = r.source
        if r.release_id <= last_seen.get(src, ""):
            continue
        new_releases.append(r)
        if r.release_id > newest.get(src, ""):
            newest[src] = r.release_id

    if newest != last_seen:
        async with session_scope() as session:
            await set_state(session, STATE_KEY, {"last_seen": newest})

    ingested = 0
    for release in new_releases:
        if await _process_release(release):
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
    asyncio.run(run_loop())


if __name__ == "__main__":
    main()
