from __future__ import annotations

import asyncio
import json
import re
import signal
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from tenacity import retry, stop_after_attempt, wait_exponential

from praxis_core.config import get_settings
from praxis_core.db.models import Source
from praxis_core.db.session import session_scope
from praxis_core.filters.cik_ticker import load_cik_ticker_map
from praxis_core.filters.edgar_items import items_pass_allowlist
from praxis_core.filters.market_cap import fetch_market_cap_usd, passes_mcap_filter
from praxis_core.logging import configure_logging, get_logger
from praxis_core.observability.events import emit_event
from praxis_core.observability.heartbeat import beat
from praxis_core.schemas.task_types import TaskType
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.time_et import et_iso, now_utc
from praxis_core.vault import conventions as vc
from praxis_core.vault.writer import atomic_write

log = get_logger("pollers.edgar_8k")


EDGAR_SEARCH_URL = (
    "https://efts.sec.gov/LATEST/search-index"
    "?q=&dateRange=custom&startdt={startdt}&enddt={enddt}&forms={form}"
)
# Extract ticker from display_names, e.g. "AMD Inc. (AMD)  (CIK 0000002488)"
TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,9}(?:,\s*[A-Z][A-Z0-9.\-]{0,9})*)\)\s*\(CIK")


@dataclass
class EdgarFiling:
    accession: str
    form_type: str
    cik: str
    title: str
    link: str
    published: datetime
    ticker: str | None = None
    items: list[str] = field(default_factory=list)
    summary: str = ""


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


def _parse_ticker_from_display(display: str) -> str | None:
    m = TICKER_RE.search(display)
    if not m:
        return None
    # If multiple (e.g. "AITX, AITXD") take the first
    return m.group(1).split(",")[0].strip()


def _build_filing_from_hit(hit: dict[str, Any]) -> EdgarFiling | None:
    src = hit.get("_source", {})
    adsh = src.get("adsh")
    form = src.get("form") or ""
    if not adsh or not form.startswith("8-K"):
        return None
    ciks = src.get("ciks") or []
    cik = ciks[0].zfill(10) if ciks else ""
    display = (src.get("display_names") or [""])[0]
    ticker = _parse_ticker_from_display(display)
    items = src.get("items") or []
    file_date = src.get("file_date") or ""
    try:
        published = datetime.fromisoformat(file_date + "T00:00:00+00:00") if file_date else now_utc()
    except ValueError:
        published = now_utc()
    # Filing index URL: /Archives/edgar/data/<cik_int>/<adsh_no_dashes>/<adsh>-index.htm
    acc_no_dashes = adsh.replace("-", "")
    link = (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_no_dashes}/{adsh}-index.htm"
        if cik
        else ""
    )
    return EdgarFiling(
        accession=adsh,
        form_type=form,
        cik=cik,
        title=f"{form} - {display}",
        link=link,
        published=published,
        ticker=ticker,
        items=list(items),
        summary="",
    )


async def _fetch_search_index(form: str, days_back: int, user_agent: str) -> list[EdgarFiling]:
    """Hit EDGAR full-text search-index for every <form> filing in the
    [today-days_back, today] window. Returns an EdgarFiling for each
    result (local dedup against `sources` happens in `_ingest_filing`).

    Unlike the legacy `getcurrent` atom feed, this endpoint is not a
    40-entry rolling window — it returns the complete list for the date
    range, so we never miss filings that were accepted between polls
    during a morning burst.
    """
    from datetime import date, timedelta

    today = date.today()
    start = today - timedelta(days=days_back)
    url = EDGAR_SEARCH_URL.format(startdt=start.isoformat(), enddt=today.isoformat(), form=form)
    raw = await _http_get(url, user_agent)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning("edgar.search_index.parse_fail", error=str(e))
        return []
    hits = (data.get("hits") or {}).get("hits") or []
    out: list[EdgarFiling] = []
    for h in hits:
        filing = _build_filing_from_hit(h)
        if filing is not None:
            out.append(filing)
    return out


async def _fetch_filing_text(filing: EdgarFiling, user_agent: str) -> str:
    """Fetch the filing's primary document. We use the filing index page as the anchor."""
    return await _http_get(filing.link, user_agent)


@dataclass
class IngestDecision:
    accept: bool
    reason: str
    matched_items: set[str] = field(default_factory=set)
    ticker: str | None = None
    market_cap_usd: int | None = None


async def _decide_ingest(
    filing: EdgarFiling,
    *,
    session,
    cik_map,
) -> IngestDecision:
    """Apply deterministic gates BEFORE fetching/writing anything. Returns accept/reject."""
    settings = get_settings()

    # 1. Item-code allowlist (8-K only; other forms pass through)
    matched: set[str] = set()
    if filing.form_type.startswith("8-K"):
        passes_items, matched = items_pass_allowlist(
            filing.items, allowlist=settings.edgar_item_allowlist_set
        )
        if not passes_items:
            return IngestDecision(
                accept=False,
                reason=f"items {filing.items or ['?']} not in allowlist",
                matched_items=matched,
            )

    # 2. Resolve ticker via CIK map if not already known
    ticker = filing.ticker or (cik_map.lookup(filing.cik) if filing.cik else None)

    # 3. Market-cap filter (if ticker resolvable)
    mcap: int | None = None
    if ticker:
        lookup = await fetch_market_cap_usd(session, ticker)
        mcap = lookup.market_cap_usd
        if not passes_mcap_filter(mcap, settings.market_cap_max_usd, keep_unknown=True):
            return IngestDecision(
                accept=False,
                reason=f"mcap ${mcap:,} > ${settings.market_cap_max_usd:,}",
                matched_items=matched,
                ticker=ticker,
                market_cap_usd=mcap,
            )

    return IngestDecision(
        accept=True,
        reason="accepted",
        matched_items=matched,
        ticker=ticker,
        market_cap_usd=mcap,
    )


async def _ingest_filing(filing: EdgarFiling, cik_map) -> bool:
    settings = get_settings()
    raw_dir = vc.raw_filing_dir(settings.vault_root, filing.form_type, filing.accession)
    filing_txt = raw_dir / "filing.txt"
    meta_json = raw_dir / "meta.json"
    dedup_key = f"filing:{filing.accession}"

    if filing_txt.exists() and meta_json.exists():
        return False

    # Seen-set check: if we've ever ingested OR rejected this accession,
    # skip. Prevents re-triaging the same filing every poll cycle (918
    # filing_rejected events/day was the symptom before this guard).
    async with session_scope() as session:
        seen = (
            await session.execute(select(Source.id).where(Source.dedup_key == dedup_key))
        ).first()
    if seen is not None:
        return False

    # Gate decisions BEFORE doing any expensive work.
    async with session_scope() as session:
        decision = await _decide_ingest(filing, session=session, cik_map=cik_map)

    if not decision.accept:
        # Persist the rejection so next poll skips it (the seen-set above).
        async with session_scope() as session:
            stmt = (
                insert(Source)
                .values(
                    dedup_key=dedup_key,
                    source_type=(
                        f"filing_rejected_{filing.form_type.lower().replace('-', '_')}"
                    ),
                    vault_path="",
                    ticker=decision.ticker,
                    extra={
                        "accession": filing.accession,
                        "form_type": filing.form_type,
                        "cik": filing.cik,
                        "reason": decision.reason,
                        "items": filing.items,
                        "market_cap_usd": decision.market_cap_usd,
                        "rejected_at": et_iso(),
                    },
                )
                .on_conflict_do_nothing(index_elements=[Source.dedup_key])
            )
            await session.execute(stmt)

        await emit_event(
            "pollers.edgar_8k",
            "filing_rejected",
            {
                "accession": filing.accession,
                "form_type": filing.form_type,
                "ticker": decision.ticker,
                "cik": filing.cik,
                "reason": decision.reason,
                "items": filing.items,
                "market_cap_usd": decision.market_cap_usd,
            },
        )
        log.info(
            "edgar.filing_rejected",
            accession=filing.accession,
            reason=decision.reason,
            ticker=decision.ticker,
        )
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
        "ticker": decision.ticker,
        "title": filing.title,
        "link": filing.link,
        "published": et_iso(filing.published),
        "ingested_at": et_iso(),
        "items": filing.items,
        "matched_items": sorted(decision.matched_items),
        "market_cap_usd": decision.market_cap_usd,
    }
    atomic_write(meta_json, json.dumps(meta, indent=2))

    rel_raw = str(filing_txt.relative_to(settings.vault_root))
    async with session_scope() as session:
        stmt = (
            insert(Source)
            .values(
                dedup_key=f"filing:{filing.accession}",
                source_type=f"filing_{filing.form_type.lower().replace('-', '_')}",
                vault_path=rel_raw,
                ticker=decision.ticker,
                extra=meta,
            )
            .on_conflict_do_nothing(index_elements=[Source.dedup_key])
        )
        await session.execute(stmt)

        # 8-Ks go straight to analyze_filing (no Haiku triage — deterministic item filter
        # already did the gating). Other forms still route through triage to allow
        # content-based classification.
        if filing.form_type.startswith("8-K"):
            # analyze_filing expects a `triage_result_path`; use meta.json as the anchor
            # since we have no triage artifact in the new flow.
            triage_path_placeholder = str(meta_json.relative_to(settings.vault_root))
            await enqueue_task(
                session,
                task_type=TaskType.ANALYZE_FILING,
                payload={
                    "accession": filing.accession,
                    "form_type": filing.form_type,
                    "ticker": decision.ticker,
                    "cik": filing.cik,
                    "triage_result_path": triage_path_placeholder,
                    "raw_path": rel_raw,
                },
                priority=0,
                dedup_key=f"analyze_filing:{filing.accession}",
            )
        else:
            await enqueue_task(
                session,
                task_type=TaskType.TRIAGE_FILING,
                payload={
                    "accession": filing.accession,
                    "form_type": filing.form_type,
                    "ticker": decision.ticker,
                    "cik": filing.cik,
                    "filing_url": filing.link,
                    "raw_path": rel_raw,
                },
                priority=0,
                dedup_key=f"triage_filing:{filing.accession}",
            )

    await emit_event(
        "pollers.edgar_8k",
        "filing_ingested",
        {
            "accession": filing.accession,
            "form_type": filing.form_type,
            "ticker": decision.ticker,
            "matched_items": sorted(decision.matched_items),
            "market_cap_usd": decision.market_cap_usd,
        },
    )
    log.info(
        "edgar.filing_ingested",
        accession=filing.accession,
        form=filing.form_type,
        ticker=decision.ticker,
        matched_items=sorted(decision.matched_items),
    )
    return True


async def poll_once() -> int:
    settings = get_settings()
    forms = set(settings.edgar_form_types_list)
    ingested = 0
    days_back = max(1, getattr(settings, "edgar_search_days_back", 2))

    # Load CIK→ticker map once per poll cycle (cached in Postgres with daily refresh).
    async with session_scope() as session:
        cik_map = await load_cik_ticker_map(session)

    for form in forms:
        try:
            filings = await _fetch_search_index(form, days_back, settings.sec_user_agent)
        except Exception as e:
            log.warning("edgar.search_index_fail", form=form, error=str(e))
            continue
        log.info("edgar.search_index", form=form, candidates=len(filings))
        for f in filings:
            if await _ingest_filing(f, cik_map):
                ingested += 1
    return ingested


async def run_loop() -> None:
    configure_logging()
    settings = get_settings()
    log.info(
        "edgar.start",
        interval_s=settings.edgar_poll_interval_s,
        forms=settings.edgar_form_types_list,
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    while not stop_event.is_set():
        try:
            count = await poll_once()
            await beat(
                "pollers.edgar_8k",
                status={"last_poll_at": et_iso(), "ingested": count},
            )
        except Exception as e:
            log.exception("edgar.loop_error", error=str(e))
            await beat(
                "pollers.edgar_8k",
                status={"last_poll_at": et_iso(), "error": str(e)[:200]},
            )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.edgar_poll_interval_s)
        except TimeoutError:
            pass

    log.info("edgar.shutdown")


def main() -> None:
    asyncio.run(run_loop())


if __name__ == "__main__":
    main()
