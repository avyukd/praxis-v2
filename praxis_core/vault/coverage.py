"""Wiki-aware coverage check (Section B D24).

Heuristic, not semantic: scans themes/ and concepts/ for files whose
frontmatter tags or title tokens match per-dimension keyword sets, with
a freshness filter (themes decay; concepts are evergreen and ignore the
window).

Feeds the orchestrator's skip-redundant-specialists logic — e.g. if
themes/ai-capex-digestion.md is fresh AND tagged macro, skip dive_macro.
Semantic coverage search via pgvector is deferred per PLAN §16.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

Dimension = Literal[
    "geopolitical",
    "macro",
    "industry",
    "moat",
    "financial",
    "capital_allocation",
]

# Per-dimension keyword sets — intentionally broad; false positives are
# cheaper than false negatives for a "skip this specialist" gate.
DIMENSION_KEYWORDS: dict[Dimension, set[str]] = {
    "geopolitical": {
        "sanctions",
        "tariff",
        "tariffs",
        "trade-war",
        "war",
        "sovereign",
        "regulatory",
        "regime",
        "iran",
        "russia",
        "china-exposure",
        "export-control",
        "geopolitical",
        "geopolitics",
        "chokepoint",
        "strait",
        "embargo",
    },
    "macro": {
        "macro",
        "inflation",
        "rate-cut",
        "rate-hike",
        "fed",
        "cycle",
        "recession",
        "yield-curve",
        "fx",
        "dollar",
        "commodity-cycle",
    },
    "industry": {
        "industry-structure",
        "porter",
        "capex-cycle",
        "consolidation",
        "oligopoly",
        "industry",
        "sector-rotation",
        "commodity-cycle",
        "supply-chain",
    },
    "moat": {
        "moat",
        "competitive-advantage",
        "switching-costs",
        "network-effect",
        "network-effects",
        "pricing-power",
        "durable-advantage",
        "brand-equity",
        "distribution-moat",
    },
    "financial": {
        "rigorous-financial",
        "quality-of-earnings",
        "cash-conversion",
        "working-capital",
        "sbc",
        "stock-based-compensation",
        "ebitda-adjustments",
        "non-gaap",
        "restated",
        "going-concern",
        "liquidity",
    },
    "capital_allocation": {
        "capital-allocation",
        "buybacks",
        "dividend",
        "mna",
        "acquisition",
        "divestiture",
        "spinoff",
        "reinvestment",
        "roiic",
        "return-on-capital",
    },
}


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
_TAG_LIST_RE = re.compile(r"^\s*tags:\s*\[([^\]]*)\]", re.MULTILINE)
_TAG_YAML_BLOCK_RE = re.compile(
    r"^\s*tags:\s*\n((?:\s+-\s*\S.*(?:\n|$))+)", re.MULTILINE
)


def _extract_tags(text: str) -> set[str]:
    """Pull YAML frontmatter tags. Handles both [a, b, c] inline and
    the multi-line `tags:\n  - a\n  - b` form."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return set()
    fm = m.group(1)
    tags: set[str] = set()
    inline = _TAG_LIST_RE.search(fm)
    if inline:
        tags.update(t.strip().strip("\"'").lower() for t in inline.group(1).split(","))
    block = _TAG_YAML_BLOCK_RE.search(fm)
    if block:
        for line in block.group(1).splitlines():
            t = line.strip().lstrip("-").strip().strip("\"'").lower()
            if t:
                tags.add(t)
    return {t for t in tags if t}


def _tokens_from_path(path: Path) -> set[str]:
    """Split slug + parent dir on hyphens/underscores. All lowercase."""
    stem = path.stem
    return {tok.lower() for tok in re.split(r"[-_]", stem) if tok}


def _file_matches_dimension(path: Path, dimension: Dimension) -> bool:
    keywords = DIMENSION_KEYWORDS[dimension]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    tags = _extract_tags(text)
    tokens = _tokens_from_path(path)
    # Slug-based keywords (with hyphens) + individual tokens both matter.
    haystack = tags | tokens | {t.replace("-", "") for t in tags}
    for kw in keywords:
        kw_l = kw.lower()
        if kw_l in haystack:
            return True
        # Also scan the file body for multi-word keywords.
        if "-" in kw_l and kw_l in text.lower():
            return True
    return False


def find_existing_coverage(
    vault_root: Path,
    ticker: str,
    dimensions: list[Dimension],
    *,
    freshness_days: int = 30,
) -> dict[Dimension, list[Path]]:
    """For each dimension, return vault files (themes/ + concepts/) that
    plausibly already cover it.

    Concepts are evergreen and always considered (freshness window ignored).
    Themes decay — only files modified within `freshness_days` are included.
    The `ticker` param is accepted for future filtering but not used in the
    keyword heuristic (all themes/concepts are company-agnostic by design).
    """
    _ = ticker  # reserved for future per-ticker relevance
    out: dict[Dimension, list[Path]] = {d: [] for d in dimensions}
    if not vault_root.exists():
        return out

    now = datetime.now(UTC)
    cutoff = now - timedelta(days=freshness_days)

    themes_dir = vault_root / "themes"
    concepts_dir = vault_root / "concepts"

    def _collect(base: Path, apply_freshness: bool) -> list[Path]:
        if not base.is_dir():
            return []
        found: list[Path] = []
        for p in base.glob("*.md"):
            if not p.is_file():
                continue
            if apply_freshness:
                try:
                    mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=UTC)
                except OSError:
                    continue
                if mtime < cutoff:
                    continue
            found.append(p)
        return found

    theme_candidates = _collect(themes_dir, apply_freshness=True)
    concept_candidates = _collect(concepts_dir, apply_freshness=False)

    for dim in dimensions:
        for p in theme_candidates + concept_candidates:
            if _file_matches_dimension(p, dim):
                out[dim].append(p)

    return out
