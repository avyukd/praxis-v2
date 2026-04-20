"""Unit tests for find_existing_coverage (D24)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from praxis_core.vault.coverage import (
    DIMENSION_KEYWORDS,
    _extract_tags,
    _tokens_from_path,
    find_existing_coverage,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _set_mtime(path: Path, days_ago: int) -> None:
    ts = (datetime.now(UTC) - timedelta(days=days_ago)).timestamp()
    os.utime(path, (ts, ts))


def test_extract_tags_inline():
    text = "---\ntype: theme\ntags: [ai-capex, macro, moat]\nstatus: active\n---\nbody"
    assert _extract_tags(text) == {"ai-capex", "macro", "moat"}


def test_extract_tags_block():
    text = "---\ntype: theme\ntags:\n  - geopolitical\n  - strait\n---\nbody"
    assert _extract_tags(text) == {"geopolitical", "strait"}


def test_extract_tags_missing_frontmatter():
    assert _extract_tags("just body text") == set()


def test_tokens_from_path_basic():
    assert _tokens_from_path(Path("ai-capex-digestion.md")) == {"ai", "capex", "digestion"}


def test_tokens_from_path_underscore():
    assert _tokens_from_path(Path("oil_and_gas.md")) == {"oil", "and", "gas"}


def test_find_coverage_matches_theme_via_tag(tmp_path):
    themes = tmp_path / "themes"
    _write(themes / "ai-capex.md", "---\ntype: theme\ntags: [macro, ai-capex]\n---\n")
    result = find_existing_coverage(tmp_path, "NVDA", ["macro"])
    assert any(p.name == "ai-capex.md" for p in result["macro"])


def test_find_coverage_matches_concept_via_slug_token(tmp_path):
    concepts = tmp_path / "concepts"
    _write(concepts / "chokepoint-economics.md", "---\ntype: concept\n---\nbody")
    # "chokepoint" is a geopolitical keyword
    result = find_existing_coverage(tmp_path, "NVDA", ["geopolitical"])
    assert any(p.name == "chokepoint-economics.md" for p in result["geopolitical"])


def test_find_coverage_no_match(tmp_path):
    themes = tmp_path / "themes"
    _write(themes / "irrelevant.md", "---\ntype: theme\ntags: [random-topic]\n---\n")
    result = find_existing_coverage(tmp_path, "NVDA", ["macro"])
    assert result["macro"] == []


def test_find_coverage_stale_theme_excluded(tmp_path):
    themes = tmp_path / "themes"
    p = themes / "old-macro-note.md"
    _write(p, "---\ntype: theme\ntags: [macro]\n---\n")
    _set_mtime(p, days_ago=45)
    result = find_existing_coverage(tmp_path, "NVDA", ["macro"], freshness_days=30)
    assert result["macro"] == []


def test_find_coverage_concepts_ignore_freshness(tmp_path):
    concepts = tmp_path / "concepts"
    p = concepts / "moat-analysis.md"
    _write(p, "---\ntype: concept\ntags: [moat]\n---\n")
    _set_mtime(p, days_ago=365)  # very old
    result = find_existing_coverage(tmp_path, "NVDA", ["moat"], freshness_days=30)
    # Concepts ignore the freshness window
    assert any(p.name == "moat-analysis.md" for p in result["moat"])


def test_find_coverage_multiple_dimensions(tmp_path):
    themes = tmp_path / "themes"
    _write(themes / "tariff-war.md", "---\ntype: theme\ntags: [geopolitical, tariffs]\n---\n")
    _write(themes / "rate-cycle.md", "---\ntype: theme\ntags: [macro]\n---\n")
    result = find_existing_coverage(
        tmp_path, "NVDA", ["geopolitical", "macro", "moat"]
    )
    assert len(result["geopolitical"]) == 1
    assert len(result["macro"]) == 1
    assert result["moat"] == []


def test_find_coverage_empty_vault(tmp_path):
    result = find_existing_coverage(tmp_path, "NVDA", ["macro", "moat"])
    assert result == {"macro": [], "moat": []}


def test_find_coverage_missing_vault():
    result = find_existing_coverage(
        Path("/nonexistent/vault"), "NVDA", ["macro"]
    )
    assert result == {"macro": []}


def test_dimension_keywords_cover_all_dimensions():
    expected = {
        "geopolitical",
        "macro",
        "industry",
        "moat",
        "financial",
        "capital_allocation",
    }
    assert set(DIMENSION_KEYWORDS.keys()) == expected
    for dim, kws in DIMENSION_KEYWORDS.items():
        assert len(kws) >= 5, f"{dim} has too few keywords"
