from __future__ import annotations

from pathlib import Path

from services.migrate.rename_map import (
    RenameEntry,
    RenameMap,
    _memo_ticker_candidate,
    _source_target_path,
    build_rename_map,
)


def _write(p: Path, content: str = "body") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_memo_ticker_extraction_known_tickers() -> None:
    assert _memo_ticker_candidate("2026-04-10-clmt-bull-test", {"CLMT", "NVDA"}) == "CLMT"
    assert _memo_ticker_candidate("2026-04-10-argx-pdufa", {"ARGX"}) == "ARGX"


def test_memo_ticker_extraction_multisegment() -> None:
    assert _memo_ticker_candidate("2026-04-10-brk-a-deep-dive", {"BRK.A", "BRK-A"}) == "BRK-A"


def test_memo_ticker_unknown_returns_none() -> None:
    assert _memo_ticker_candidate("2026-04-10-hormuz-scenarios", {"NVDA"}) is None


def test_source_flatten_with_date_prefix() -> None:
    out = _source_target_path("80_sources/2026/04/2026-04-18_ft.com_slug.md")
    assert out == "_raw/desktop_clips/2026-04-18/ft.com_slug.md"


def test_source_flatten_without_date() -> None:
    out = _source_target_path("80_sources/2026/04/some-clip.md")
    assert out.startswith("_raw/desktop_clips/2026-04-")
    assert out.endswith("/some-clip.md")


def test_build_rename_map_basic(tmp_path: Path) -> None:
    src = tmp_path / "vault"
    _write(src / "10_themes/strait-of-hormuz.md", "# hormuz")
    _write(src / "15_concepts/circle-of-competence.md", "# coc")
    _write(src / "25_people/buffett-warren.md", "# buff")
    _write(src / "60_questions/argx-deep-dive.md", "# q")
    _write(src / "20_companies/NVDA/notes.md", "# NVDA")
    _write(src / "20_companies/NVDA/data/fundamentals.json", "{}")
    _write(src / "40_memos/2026-04-10-nvda-thesis.md", "# memo")
    _write(src / "40_memos/2026-04-10-hormuz-scenarios.md", "# cross-cut")
    _write(
        src / "30_theses/nvda-ai-capex.md",
        "---\ntype: thesis\nticker: NVDA\nstatus: active\n---\n\nbody\n",
    )
    _write(src / "80_sources/2026/04/2026-04-18_ft_article.md", "clipped")
    _write(src / "00_inbox/stuff.md", "drop me")
    _write(src / "90_meta/agenda.md", "drop me")
    _write(src / "INDEX.md", "drop me")

    rm = build_rename_map(src, known_tickers={"NVDA"})

    by_old = {e.old_path: e for e in rm.entries}

    assert by_old["10_themes/strait-of-hormuz.md"].new_path == "themes/strait-of-hormuz.md"
    assert (
        by_old["15_concepts/circle-of-competence.md"].new_path == "concepts/circle-of-competence.md"
    )
    assert by_old["25_people/buffett-warren.md"].new_path == "people/buffett-warren.md"
    assert by_old["60_questions/argx-deep-dive.md"].new_path == "questions/argx-deep-dive.md"
    assert by_old["20_companies/NVDA/notes.md"].new_path == "companies/NVDA/notes.md"
    assert (
        by_old["40_memos/2026-04-10-nvda-thesis.md"].new_path
        == "companies/NVDA/memos/2026-04-10-nvda-thesis.md"
    )
    assert (
        by_old["40_memos/2026-04-10-hormuz-scenarios.md"].new_path
        == "memos/2026-04-10-hormuz-scenarios.md"
    )
    assert by_old["30_theses/nvda-ai-capex.md"].new_path == "companies/NVDA/thesis.md"
    assert by_old["30_theses/nvda-ai-capex.md"].kind == "thesis_merge"
    assert (
        by_old["80_sources/2026/04/2026-04-18_ft_article.md"].new_path
        == "_raw/desktop_clips/2026-04-18/ft_article.md"
    )
    assert by_old["00_inbox/stuff.md"].new_path is None
    assert by_old["90_meta/agenda.md"].new_path is None
    assert by_old["INDEX.md"].new_path is None


def test_stem_lookup() -> None:
    rm = RenameMap()
    rm.add(
        RenameEntry(
            old_path="10_themes/strait-of-hormuz.md",
            new_path="themes/strait-of-hormuz.md",
            kind="theme",
        )
    )
    rm.add(
        RenameEntry(
            old_path="20_companies/NVDA/notes.md",
            new_path="companies/NVDA/notes.md",
            kind="company_note",
        )
    )

    # Full relative path
    assert rm.lookup("10_themes/strait-of-hormuz") == "themes/strait-of-hormuz"
    # With .md
    assert rm.lookup("10_themes/strait-of-hormuz.md") == "themes/strait-of-hormuz"
    # Stem-only (Obsidian-style)
    assert rm.lookup("strait-of-hormuz") == "themes/strait-of-hormuz"
    # Nonexistent
    assert rm.lookup("does-not-exist") is None
