from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from praxis_core.logging import get_logger
from praxis_core.newswire.models import PressRelease
from praxis_core.newswire.rate import NEWSWIRE_RATE

log = get_logger("newswire.cnw")

USER_AGENT = "praxis-v2/1.0 (+https://praxis.local/research)"
CNW_BASE = "https://www.newswire.ca"
# Public-company-specific listing — the generic /news-releases/ endpoint
# returns a nav page with no actual release cards.
CNW_LISTING_URL = f"{CNW_BASE}/news-releases/all-public-company-news/"

_TICKER_RE = re.compile(
    r"\((?P<exchange>TSX|TSXV|TSX-V)\s*:\s*(?P<ticker>[A-Z][A-Z0-9.]*)\)",
    re.IGNORECASE,
)


@retry(wait=wait_exponential(multiplier=1, min=2, max=30), stop=stop_after_attempt(3))
async def _http_get(url: str) -> str:
    await NEWSWIRE_RATE.consume()
    async with httpx.AsyncClient(
        timeout=30.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text


def _extract_release_id(url: str) -> str:
    """CNW release URLs end in a slug + numeric id + .html, e.g.
    /news-releases/biorem-reports-65-3-million-order-backlog-873169666.html.
    Dates/numbers earlier in the slug are skipped by picking the LAST 6+ digit
    run."""
    last = url.rstrip("/").split("/")[-1] if "/" in url else url
    matches = re.findall(r"(\d{6,})", last)
    return matches[-1] if matches else ""


def _extract_ticker(text: str) -> tuple[str, str]:
    m = _TICKER_RE.search(text)
    if m:
        ex = m.group("exchange").upper()
        if ex == "TSX-V":
            ex = "TSXV"
        return m.group("ticker").upper(), ex
    return "", ""


def parse_cnw_listing(html: str) -> list[PressRelease]:
    """Parse CNW's public-company listing page.

    Each release lives in a `div.row.newsCards` wrapper containing an
    anchor → h3 (title with <small>ET-time</small> prefix) and a
    p.remove-outline with the snippet body where the ticker appears.
    """
    soup = BeautifulSoup(html, "lxml")
    items: list[PressRelease] = []

    cards = soup.select("div.row.newsCards")
    for card in cards:
        a = card.find("a", href=True)
        if not a:
            continue
        href = a["href"]
        if not href.startswith("http"):
            href = CNW_BASE + href

        h3 = a.find("h3")
        title = h3.get_text(" ", strip=True) if h3 else a.get_text(" ", strip=True)

        time_sm = h3.find("small") if h3 else None
        published_at = time_sm.get_text(strip=True) if time_sm else ""

        desc_el = a.find("p", class_="remove-outline")
        desc = desc_el.get_text(" ", strip=True) if desc_el else ""

        rid = _extract_release_id(href)
        if not rid:
            continue

        # Ticker often appears in the description body, not the title.
        ticker, exchange = _extract_ticker(desc)
        if not ticker:
            ticker, exchange = _extract_ticker(title)

        items.append(
            PressRelease(
                release_id=f"cnw-{rid}",
                title=title,
                url=href,
                published_at=published_at,
                source="cnw",
                ticker=ticker,
                exchange=exchange,
            )
        )
    return items


async def poll_cnw(pages: int = 2) -> list[PressRelease]:
    releases: list[PressRelease] = []
    for page in range(1, pages + 1):
        url = CNW_LISTING_URL if page == 1 else f"{CNW_LISTING_URL}?page={page}"
        try:
            html = await _http_get(url)
        except Exception as e:
            log.warning("cnw.fetch_fail", page=page, error=str(e))
            continue
        releases.extend(parse_cnw_listing(html))
    return releases


async def fetch_cnw_text(url: str) -> str:
    html = await _http_get(url)
    soup = BeautifulSoup(html, "lxml")
    body = (
        soup.find("div", class_="release-body")
        or soup.find("div", class_="content-body")
        or soup.find("article")
    )
    if not body:
        paragraphs = soup.find_all("p")
        return "\n\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
    return body.get_text(separator="\n", strip=True)
