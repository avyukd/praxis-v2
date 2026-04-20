from __future__ import annotations

from difflib import SequenceMatcher

from praxis_core.newswire.models import PressRelease

SIMILARITY_THRESHOLD = 0.75


def _similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= SIMILARITY_THRESHOLD


def dedup_releases(releases: list[PressRelease]) -> list[PressRelease]:
    """Remove duplicate releases across sources (same ticker + similar title).

    Keeps first-seen; drops later duplicates. Items without a ticker are
    passed through unchanged (dedup is only meaningful when we can attribute).
    """
    if not releases:
        return []
    seen: list[tuple[str, str]] = []
    result: list[PressRelease] = []
    for r in releases:
        if not r.ticker:
            result.append(r)
            continue
        dup = any(r.ticker == t and _similar(r.title, ttl) for t, ttl in seen)
        if not dup:
            seen.append((r.ticker, r.title))
            result.append(r)
    return result


async def fetch_release_text(url: str, source: str) -> str:
    """Dispatcher that routes to the correct per-source fetcher."""
    if source == "gnw":
        from praxis_core.newswire.gnw import fetch_gnw_text

        return await fetch_gnw_text(url)
    if source == "cnw":
        from praxis_core.newswire.cnw import fetch_cnw_text

        return await fetch_cnw_text(url)
    if source == "newsfile":
        from praxis_core.newswire.newsfile import fetch_newsfile_text

        return await fetch_newsfile_text(url)
    raise ValueError(f"unknown newswire source: {source}")
