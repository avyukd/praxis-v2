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

log = get_logger("newswire.newsfile")

USER_AGENT = "praxis-v2/1.0 (+https://praxis.local/research)"

DEFAULT_CATEGORIES = [
    "mining-metals",
    "technology",
    "oil-gas",
    "cannabis",
    "biotech-pharma",
    "clean-technology",
]

_TICKER_RE = re.compile(r"\((?P<exchange>TSX|TSXV|TSX-V)\s*:\s*(?P<ticker>[A-Z][A-Z0-9.]*)\)")


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
    m = re.search(r"/release/(\d+)", url)
    return m.group(1) if m else ""


def _extract_ticker(text: str) -> tuple[str, str]:
    m = _TICKER_RE.search(text)
    if m:
        ex = m.group("exchange")
        if ex == "TSX-V":
            ex = "TSXV"
        return m.group("ticker"), ex
    return "", ""


def parse_newsfile_feed(xml_text: str) -> list[PressRelease]:
    items: list[PressRelease] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    for item in root.iter("item"):
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        pub_date = item.findtext("pubDate", "")
        description = item.findtext("description", "")

        rid = _extract_release_id(link)
        if not rid:
            continue

        ticker, exchange = _extract_ticker(f"{title} {description}")

        published_at = ""
        if pub_date:
            try:
                dt = parsedate_to_datetime(pub_date)
                published_at = dt.isoformat()
            except (TypeError, ValueError):
                published_at = pub_date

        items.append(
            PressRelease(
                release_id=f"newsfile-{rid}",
                title=title,
                url=link,
                published_at=published_at,
                source="newsfile",
                ticker=ticker,
                exchange=exchange,
            )
        )
    return items


async def poll_newsfile(categories: list[str] | None = None) -> list[PressRelease]:
    cats = categories or DEFAULT_CATEGORIES
    releases: list[PressRelease] = []
    for cat in cats:
        url = f"https://feeds.newsfilecorp.com/industry/{cat}"
        try:
            content = await _http_get(url)
        except Exception as e:
            log.warning("newsfile.fetch_fail", category=cat, error=str(e))
            continue
        releases.extend(parse_newsfile_feed(content))
    return releases


async def fetch_newsfile_text(url: str) -> str:
    html = await _http_get(url)
    soup = BeautifulSoup(html, "lxml")
    body = soup.find("div", class_="release-body") or soup.find("article")
    if not body:
        paragraphs = soup.find_all("p")
        return "\n\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
    return body.get_text(separator="\n", strip=True)
