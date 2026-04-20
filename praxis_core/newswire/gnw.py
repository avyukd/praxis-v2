from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from praxis_core.logging import get_logger
from praxis_core.newswire.models import PressRelease
from praxis_core.newswire.rate import NEWSWIRE_RATE

log = get_logger("newswire.gnw")

USER_AGENT = "praxis-v2/1.0 (+https://praxis.local/research)"

GNW_US_FEEDS = [
    "https://www.globenewswire.com/RssFeed/exchange/NYSE",
    "https://www.globenewswire.com/RssFeed/exchange/NASDAQ",
]

GNW_CA_FEEDS = [
    # GlobeNewswire's /exchange/TSX and /exchange/TSXV feeds return 0 items
    # (empty <channel>) despite returning HTTP 200. The country-level feed
    # is the working one; ticker+exchange are parsed from <category>
    # stock-tags per release.
    "https://www.globenewswire.com/RssFeed/country/Canada",
]

_TICKER_PAREN_RE = re.compile(
    r"\((?P<exchange>TSX|TSXV|TSX-V|NYSE|NASDAQ)\s*:\s*(?P<ticker>[A-Za-z][A-Za-z0-9.]*)\)",
    re.IGNORECASE,
)

_TICKER_BARE_RE = re.compile(
    r"(?P<exchange>TSX|TSXV|TSX-V|NYSE|NASDAQ)\s*:\s*(?P<ticker>[A-Za-z][A-Za-z0-9.]*)",
    re.IGNORECASE,
)


@retry(wait=wait_exponential(multiplier=1, min=2, max=30), stop=stop_after_attempt(4))
async def _http_get(url: str) -> str:
    await NEWSWIRE_RATE.consume()
    async with httpx.AsyncClient(
        timeout=30.0,
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
        follow_redirects=True,
    ) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text


def _exchange_from_feed_url(url: str) -> str:
    parts = url.rstrip("/").split("/")
    for i, part in enumerate(parts):
        if part.lower() == "exchange" and i + 1 < len(parts):
            return parts[i + 1].upper()
    return ""


def _extract_release_id(url: str) -> str:
    parts = url.split("/")
    for i, part in enumerate(parts):
        if part == "news-release" and i + 4 < len(parts):
            c = parts[i + 4]
            if c.isdigit():
                return c
    return ""


def _extract_ticker_from_category(item: ET.Element) -> tuple[str, str]:
    for cat in item.findall("category"):
        domain = cat.get("domain", "")
        if "rss/stock" in domain and cat.text:
            m = _TICKER_BARE_RE.search(cat.text)
            if m:
                ex = m.group("exchange").upper()
                if ex == "TSX-V":
                    ex = "TSXV"
                return m.group("ticker").upper(), ex
    return "", ""


def _extract_ticker_from_text(text: str) -> tuple[str, str]:
    m = _TICKER_PAREN_RE.search(text)
    if m:
        ex = m.group("exchange").upper()
        if ex == "TSX-V":
            ex = "TSXV"
        return m.group("ticker").upper(), ex
    return "", ""


def parse_gnw_feed(xml_text: str, feed_exchange: str = "") -> list[PressRelease]:
    items: list[PressRelease] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning("gnw.parse_fail", error=str(e))
        return items

    for item in root.iter("item"):
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        pub_date = item.findtext("pubDate", "")
        description = item.findtext("description", "")

        rid = _extract_release_id(link)
        if not rid:
            continue

        ticker, exchange = _extract_ticker_from_category(item)
        if not ticker:
            ticker, exchange = _extract_ticker_from_text(f"{title} {description}")
        if not exchange and feed_exchange:
            exchange = feed_exchange

        published_at = ""
        if pub_date:
            try:
                dt = parsedate_to_datetime(pub_date)
                published_at = dt.isoformat()
            except (TypeError, ValueError):
                published_at = pub_date

        items.append(
            PressRelease(
                release_id=f"gnw-{rid}",
                title=title,
                url=link,
                published_at=published_at,
                source="gnw",
                ticker=ticker,
                exchange=exchange,
            )
        )
    return items


async def poll_gnw(feed_urls: list[str]) -> list[PressRelease]:
    releases: list[PressRelease] = []
    for url in feed_urls:
        feed_exchange = _exchange_from_feed_url(url)
        try:
            content = await _http_get(url)
        except Exception as e:
            log.warning("gnw.fetch_fail", url=url, error=str(e))
            continue
        releases.extend(parse_gnw_feed(content, feed_exchange=feed_exchange))
    return releases


async def fetch_gnw_text(url: str) -> str:
    """Fetch + extract main body text from a GNW news-release page."""
    html = await _http_get(url)
    soup = BeautifulSoup(html, "lxml")
    body = (
        soup.find("div", class_="main-body-container")
        or soup.find("article")
        or soup.find("div", id="main-body-container")
    )
    if not body:
        paragraphs = soup.find_all("p")
        return "\n\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
    return body.get_text(separator="\n", strip=True)
