from __future__ import annotations

import asyncio
import json
import re
import signal
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser
import httpx
from sqlalchemy.dialects.postgresql import insert
from tenacity import retry, stop_after_attempt, wait_exponential

from praxis_core.config import get_settings
from praxis_core.db.models import Source
from praxis_core.db.session import session_scope
from praxis_core.logging import configure_logging, get_logger
from praxis_core.observability.events import emit_event
from praxis_core.observability.heartbeat import beat
from praxis_core.schemas.task_types import TaskType
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.vault import conventions as vc
from praxis_core.vault.writer import atomic_write

log = get_logger("pollers.edgar_8k")


EDGAR_FEED_URL_TEMPLATE = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type={form}&company=&dateb=&owner=include&count={count}&output=atom"
)
ACCESSION_RE = re.compile(r"accession-number=(\S+)|Accession Number:\s*(\S+)")


@dataclass
class EdgarFiling:
    accession: str
    form_type: str
    cik: str
    title: str
    link: str
    published: datetime
    ticker: str | None = None


class RateBucket:
    """Simple token bucket for SEC's 10 req/sec politeness rule."""

    def __init__(self, tokens_per_sec: float = 8.0) -> None:
        self.interval = 1.0 / tokens_per_sec
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def consume(self) -> None:
        async with self._lock:
            now = asyncio.get_running_loop().time()
            wait = self.interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = asyncio.get_running_loop().time()


_RATE = RateBucket()


@retry(wait=wait_exponential(multiplier=1, min=2, max=30), stop=stop_after_attempt(4))
async def _http_get(url: str, user_agent: str) -> str:
    await _RATE.consume()
    async with httpx.AsyncClient(
        timeout=30.0,
        headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


def _parse_accession_from_link(link: str, title: str) -> str | None:
    for pat in (r"accession-number=(\S+)", r"/(\d{10}-\d{2}-\d{6})"):
        m = re.search(pat, link)
        if m:
            return m.group(1)
    m = re.search(r"(\d{10}-\d{2}-\d{6})", title)
    if m:
        return m.group(1)
    return None


def _parse_cik_from_link(link: str) -> str | None:
    m = re.search(r"CIK=(\d+)", link)
    if m:
        return m.group(1).zfill(10)
    m = re.search(r"/cgi-bin/browse-edgar\?.*CIK=([0-9]+)", link)
    if m:
        return m.group(1).zfill(10)
    return None


def _parse_ticker_from_entry(entry: dict[str, Any]) -> str | None:
    # EDGAR atom doesn't reliably carry ticker; we parse title/summary heuristically.
    title = entry.get("title", "")
    m = re.search(r"\((\w{1,6})\)", title)
    if m:
        t = m.group(1).upper()
        if t not in {"FORM", "8-K", "10-K", "10-Q", "NT"}:
            return t
    return None


def _parse_feed(content: str, form_filter: set[str]) -> list[EdgarFiling]:
    feed = feedparser.parse(content)
    out: list[EdgarFiling] = []
    for entry in feed.entries:
        raw_title = entry.get("title", "")
        raw_link = entry.get("link", "")
        title: str = raw_title if isinstance(raw_title, str) else str(raw_title or "")
        link: str = raw_link if isinstance(raw_link, str) else str(raw_link or "")
        accession = _parse_accession_from_link(link, title)
        if not accession:
            continue
        form = title.split(" ", 1)[0].strip()
        if form_filter and form not in form_filter:
            continue
        cik = _parse_cik_from_link(link) or ""
        ticker = _parse_ticker_from_entry(dict(entry))
        published_raw_any = entry.get("updated") or entry.get("published")
        published_raw = (
            published_raw_any if isinstance(published_raw_any, str) else None
        )
        try:
            published = (
                datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
                if published_raw
                else datetime.now(timezone.utc)
            )
        except (ValueError, AttributeError):
            published = datetime.now(timezone.utc)
        out.append(
            EdgarFiling(
                accession=accession,
                form_type=form,
                cik=cik,
                title=title,
                link=link,
                published=published,
                ticker=ticker,
            )
        )
    return out


async def _fetch_filing_text(filing: EdgarFiling, user_agent: str) -> str:
    """Fetch the filing's primary document. We use the filing index page as the anchor."""
    return await _http_get(filing.link, user_agent)


async def _ingest_filing(filing: EdgarFiling) -> bool:
    settings = get_settings()
    raw_dir = vc.raw_filing_dir(settings.vault_root, filing.form_type, filing.accession)
    filing_txt = raw_dir / "filing.txt"
    meta_json = raw_dir / "meta.json"

    if filing_txt.exists() and meta_json.exists():
        return False

    try:
        content = await _fetch_filing_text(filing, settings.sec_user_agent)
    except Exception as e:
        log.warning("edgar.fetch_fail", accession=filing.accession, error=str(e))
        return False

    atomic_write(filing_txt, content)
    meta = {
        "accession": filing.accession,
        "form_type": filing.form_type,
        "cik": filing.cik,
        "ticker": filing.ticker,
        "title": filing.title,
        "link": filing.link,
        "published": filing.published.isoformat(),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write(meta_json, json.dumps(meta, indent=2))

    rel_raw = str(filing_txt.relative_to(settings.vault_root))
    async with session_scope() as session:
        stmt = insert(Source).values(
            dedup_key=f"filing:{filing.accession}",
            source_type=f"filing_{filing.form_type.lower().replace('-', '_')}",
            vault_path=rel_raw,
            ticker=filing.ticker,
            extra=meta,
        ).on_conflict_do_nothing(index_elements=[Source.dedup_key])
        await session.execute(stmt)

        priority = 0  # P0 for Monday filings
        await enqueue_task(
            session,
            task_type=TaskType.TRIAGE_FILING,
            payload={
                "accession": filing.accession,
                "form_type": filing.form_type,
                "ticker": filing.ticker,
                "cik": filing.cik,
                "filing_url": filing.link,
                "raw_path": rel_raw,
            },
            priority=priority,
            dedup_key=f"triage_filing:{filing.accession}",
        )

    await emit_event(
        "pollers.edgar_8k",
        "filing_ingested",
        {
            "accession": filing.accession,
            "form_type": filing.form_type,
            "ticker": filing.ticker,
        },
    )
    log.info(
        "edgar.filing_ingested",
        accession=filing.accession,
        form=filing.form_type,
        ticker=filing.ticker,
    )
    return True


async def poll_once() -> int:
    settings = get_settings()
    forms = set(settings.edgar_form_types_list)
    ingested = 0
    for form in forms:
        url = EDGAR_FEED_URL_TEMPLATE.format(form=form, count=40)
        try:
            content = await _http_get(url, settings.sec_user_agent)
        except Exception as e:
            log.warning("edgar.feed_fail", form=form, error=str(e))
            continue
        filings = _parse_feed(content, form_filter={form})
        for f in filings:
            if await _ingest_filing(f):
                ingested += 1
    return ingested


async def run_loop() -> None:
    configure_logging()
    settings = get_settings()
    log.info("edgar.start", interval_s=settings.edgar_poll_interval_s, forms=settings.edgar_form_types_list)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    while not stop_event.is_set():
        try:
            count = await poll_once()
            await beat(
                "pollers.edgar_8k",
                status={"last_poll_at": datetime.now(timezone.utc).isoformat(), "ingested": count},
            )
        except Exception as e:
            log.exception("edgar.loop_error", error=str(e))
            await beat(
                "pollers.edgar_8k",
                status={"last_poll_at": datetime.now(timezone.utc).isoformat(), "error": str(e)[:200]},
            )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.edgar_poll_interval_s)
        except asyncio.TimeoutError:
            pass

    log.info("edgar.shutdown")


def main() -> None:
    asyncio.run(run_loop())


if __name__ == "__main__":
    main()
