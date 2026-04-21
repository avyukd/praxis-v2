"""Persist web-fetched content as durable vault sources.

Every material source discovered during `gather_sources` (or any
research run) lands at `_raw/manual/<YYYY-MM-DD>/<slug>.md` with
frontmatter tracking url, title, site, fetch_time, publish_date
if known. Dedup by URL hash so repeated runs don't duplicate.

These files become addressable wikilinks that compile / answer /
synthesize handlers cite.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse

from praxis_core.logging import get_logger
from praxis_core.time_et import et_iso, now_et
from praxis_core.vault.writer import write_markdown_with_frontmatter

log = get_logger("vault.sources")


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:12]


def _slugify(text: str, max_len: int = 60) -> str:
    s = re.sub(r"[^a-z0-9\s-]", "", text.lower())
    s = re.sub(r"\s+", "-", s).strip("-")
    return s[:max_len] or "source"


def _site_of(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def persist_web_source(
    vault_root: Path,
    *,
    url: str,
    title: str,
    body_text: str,
    site: str | None = None,
    publish_date: str | None = None,
    investigation_handle: str | None = None,
    related_nodes: list[str] | None = None,
    max_body_chars: int = 20000,
) -> Path | None:
    """Write a source file. Dedup by url-hash in the day's folder.

    Returns the path if newly written, or None if already persisted.
    """
    if not url or not body_text:
        return None
    uhash = _url_hash(url)
    today = now_et().strftime("%Y-%m-%d")
    day_dir = vault_root / "_raw" / "manual" / today
    day_dir.mkdir(parents=True, exist_ok=True)

    for existing in day_dir.glob(f"*-{uhash}.md"):
        log.info("sources.dedup", url=url[:80], existing=str(existing.name))
        return None

    slug = _slugify(title) or _slugify(_site_of(url)) or "source"
    filename = f"{slug}-{uhash}.md"
    out_path = day_dir / filename

    body = body_text[:max_body_chars].strip()
    md_body = (
        f"# {title}\n\n"
        f"**Source:** {url}\n\n"
        f"**Site:** {site or _site_of(url)}\n\n"
        f"{body}\n"
    )

    meta: dict = {
        "type": "source",
        "url": url,
        "title": title[:240],
        "site": site or _site_of(url),
        "fetch_time": et_iso(),
        "url_hash": uhash,
        "tags": ["source", "manual_fetch"],
    }
    if publish_date:
        meta["publish_date"] = publish_date
    if investigation_handle:
        meta["investigation"] = investigation_handle
    if related_nodes:
        meta["related_nodes"] = list(related_nodes)

    try:
        write_markdown_with_frontmatter(out_path, body=md_body, metadata=meta)
        log.info(
            "sources.written",
            url=url[:80],
            path=str(out_path.relative_to(vault_root)),
        )
        return out_path
    except OSError as e:
        log.warning("sources.write_fail", url=url[:80], error=str(e))
        return None
