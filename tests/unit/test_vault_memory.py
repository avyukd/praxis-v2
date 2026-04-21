"""Tests for praxis_core.vault.memory — stage-1 keyword filter + rerank fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from praxis_core.vault.memory import (
    VaultHit,
    _score_overlap,
    _stage1_candidates,
    _tokenize,
    clear_cache,
    search_vault_memory,
)


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "themes").mkdir()
    (tmp_path / "questions").mkdir()
    (tmp_path / "concepts").mkdir()
    (tmp_path / "memos").mkdir()
    (tmp_path / "companies" / "ABC").mkdir(parents=True)
    (tmp_path / "_raw" / "manual" / "2026-04-20").mkdir(parents=True)
    clear_cache()
    return tmp_path


def _write_md(p: Path, title: str, body: str, tags: list[str] | None = None) -> None:
    fm = "---\n"
    fm += f"title: {title}\n"
    if tags:
        fm += "tags:\n"
        for t in tags:
            fm += f"  - {t}\n"
    fm += "---\n"
    p.write_text(fm + body)


def test_tokenize_strips_stopwords():
    tokens = _tokenize("The strait of Hormuz and fertilizer")
    assert "strait" in tokens
    assert "hormuz" in tokens
    assert "fertilizer" in tokens
    assert "the" not in tokens
    assert "of" not in tokens
    assert "and" not in tokens


def test_tokenize_drops_single_char():
    assert "a" not in _tokenize("a b cd")
    assert "cd" in _tokenize("a b cd")


def test_score_overlap_basic():
    q = {"alpha", "beta", "gamma"}
    d = {"alpha", "delta"}
    assert _score_overlap(q, d) == pytest.approx(1 / 3)


def test_score_overlap_empty_returns_zero():
    assert _score_overlap(set(), {"alpha"}) == 0
    assert _score_overlap({"alpha"}, set()) == 0


def test_stage1_returns_top_scored_by_overlap(vault):
    _write_md(
        vault / "themes" / "hormuz.md",
        "Strait of Hormuz",
        "The Hormuz chokepoint controls Gulf fertilizer exports.",
        tags=["geopolitics", "commodities"],
    )
    _write_md(
        vault / "themes" / "unrelated.md",
        "European banking",
        "Retail banking in Germany.",
    )
    _write_md(
        vault / "questions" / "hormuz-q.md",
        "Which fertilizer producers are most exposed to Hormuz?",
        "Exposure scan for fertilizer manufacturers.",
    )

    hits = _stage1_candidates(vault, "hormuz fertilizer", ("themes", "questions"))
    assert len(hits) == 2
    # hormuz theme has more term overlap than the question
    slugs = [h.path for h in hits]
    assert "themes/hormuz.md" in slugs
    assert "questions/hormuz-q.md" in slugs
    assert "themes/unrelated.md" not in [h.path for h in hits]


def test_stage1_scope_filter_excludes_other_areas(vault):
    _write_md(
        vault / "themes" / "hormuz.md",
        "Strait of Hormuz",
        "hormuz commentary",
    )
    _write_md(
        vault / "memos" / "hormuz-memo.md",
        "Hormuz basket memo",
        "hormuz equities",
    )
    hits = _stage1_candidates(vault, "hormuz", ("themes",))
    assert all(h.node_type == "theme" for h in hits)


def test_stage1_companies_scope_finds_notes_md(vault):
    _write_md(
        vault / "companies" / "ABC" / "notes.md",
        "ABC Corp",
        "ABC is a fertilizer producer.",
        tags=["fertilizer"],
    )
    hits = _stage1_candidates(vault, "fertilizer", ("companies",))
    assert len(hits) == 1
    assert hits[0].path == "companies/ABC/notes.md"
    assert hits[0].node_type == "company"


def test_stage1_sources_scope_finds_raw_manual(vault):
    _write_md(
        vault / "_raw" / "manual" / "2026-04-20" / "gulf-report.md",
        "Gulf Exports Q1",
        "Hormuz fertilizer data.",
    )
    hits = _stage1_candidates(vault, "hormuz", ("sources",))
    assert len(hits) == 1
    assert hits[0].node_type == "source"


def test_stage1_no_overlap_returns_empty(vault):
    _write_md(
        vault / "themes" / "zinc.md",
        "Zinc supply chain",
        "zinc mining and smelting",
    )
    hits = _stage1_candidates(vault, "hormuz fertilizer", ("themes",))
    assert hits == []


@pytest.mark.asyncio
async def test_search_vault_memory_with_skip_rerank(vault):
    """skip_rerank=True returns stage-1 results unchanged."""
    _write_md(vault / "themes" / "x.md", "X theme", "strait hormuz fertilizer content")
    _write_md(vault / "themes" / "y.md", "Y theme", "unrelated content")
    hits = await search_vault_memory(
        vault, "hormuz fertilizer", limit=5, skip_rerank=True
    )
    assert len(hits) == 1
    assert hits[0].title == "X theme"


@pytest.mark.asyncio
async def test_search_vault_memory_caches_repeated_queries(vault):
    """Second call within TTL should hit cache (no re-walk)."""
    _write_md(vault / "themes" / "x.md", "X", "hormuz")
    first = await search_vault_memory(vault, "hormuz", skip_rerank=True)
    # Delete the file — if cache works, second call still returns it.
    (vault / "themes" / "x.md").unlink()
    second = await search_vault_memory(vault, "hormuz", skip_rerank=True)
    assert [h.path for h in first] == [h.path for h in second]
    clear_cache()
    third = await search_vault_memory(vault, "hormuz", skip_rerank=True)
    assert third == []


@pytest.mark.asyncio
async def test_search_vault_memory_rerank_fallback_on_failure(vault, monkeypatch):
    """If stage-2 rerank returns None (rate-limited / parse fail), stage-1 results still returned."""
    _write_md(vault / "themes" / "x.md", "X", "hormuz fertilizer")

    async def fake_rerank(*_args, **_kwargs):
        return None

    monkeypatch.setattr("praxis_core.vault.memory._stage2_rerank", fake_rerank)
    hits = await search_vault_memory(vault, "hormuz fertilizer", limit=5)
    assert len(hits) == 1
    assert hits[0].path == "themes/x.md"


def test_vault_hit_to_dict_roundtrip():
    h = VaultHit(
        path="themes/x.md",
        node_type="theme",
        title="X",
        snippet="body",
        relevance_score=0.753,
        why_relevant="matched on x",
        tags=["a", "b"],
    )
    d = h.to_dict()
    assert d["path"] == "themes/x.md"
    assert d["relevance_score"] == 0.753  # rounded to 3
    assert d["tags"] == ["a", "b"]
