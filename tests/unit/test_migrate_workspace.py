from __future__ import annotations

from pathlib import Path

from services.migrate.workspace_migrator import (
    _extract_ticker_from_memo,
    _parse_date_from_body,
    migrate_workspace,
)


def test_parse_date_bolded() -> None:
    body = "# Memo\n**Date:** 2026-04-16\n**Price:** ~$3.55"
    assert _parse_date_from_body(body) == "2026-04-16"


def test_parse_date_plain() -> None:
    body = "Date: 2026-05-01\nSome content"
    assert _parse_date_from_body(body) == "2026-05-01"


def test_parse_date_missing() -> None:
    assert _parse_date_from_body("just some memo body") is None


def test_extract_ticker_from_memo_heading() -> None:
    body = "# ACHV — Investment Memo\n\nbody"
    assert _extract_ticker_from_memo(body) == "ACHV"


def test_extract_ticker_from_memo_dash() -> None:
    body = "# NVDA - thesis\n"
    assert _extract_ticker_from_memo(body) == "NVDA"


def _build_ws(
    root: Path, ticker: str, *, with_memo: bool = True, with_reports: bool = True
) -> None:
    td = root / ticker
    td.mkdir(parents=True)
    if with_memo:
        (td / "memo.md").write_text(
            f"# {ticker} — Investment Memo\n\n**Date:** 2026-04-16\n\n## Thesis\n\ncontent\n"
        )
    if with_reports:
        (td / "rigorous-financial-analyst.md").write_text(f"# {ticker} financials\n\nnumbers.")
        (td / "business-moat-analyst.md").write_text(f"# {ticker} moat\n\nmoat.")
    (td / "coordinator_log.md").write_text("- tick 1\n- tick 2\n")
    (td / "data" / "fundamentals").mkdir(parents=True, exist_ok=True)
    (td / "data" / "fundamentals" / "summary.md").write_text("# summary")
    macro = td / "macro"
    macro.mkdir()
    (macro / "3-3-2026-macro-thoughts.md").write_text("shared macro note content")


def test_migrate_workspace_full(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    target = tmp_path / "vault"
    ws.mkdir()
    _build_ws(ws, "ACHV")
    _build_ws(ws, "NVDA")
    # Third ticker shares identical macro file
    _build_ws(ws, "AAPL")

    # Also add a skipped ticker with no content
    (ws / "EMPTY").mkdir()

    report = migrate_workspace(ws, target)

    assert report.tickers_with_memo == 3
    assert report.tickers_with_analyst_reports == 3
    # Macro dedup: 3 tickers have identical macro content → 1 unique, 2 duplicates
    assert report.macro_unique == 1
    assert report.macro_duplicates_dropped == 2
    assert "EMPTY" in report.skipped_tickers

    # Check file placements
    assert (target / "companies/ACHV/memos/2026-04-16-memo.md").exists()
    assert (target / "companies/NVDA/dives/financial-rigorous.md").exists()
    assert (target / "companies/NVDA/dives/business-moat.md").exists()
    assert (target / "companies/AAPL/journal.md").exists()
    assert (target / "companies/NVDA/data/fundamentals/summary.md").exists()
    assert (target / "memos/macro").exists()
    # Exactly one macro file written
    macro_files = list((target / "memos/macro").glob("*.md"))
    assert len(macro_files) == 1


def test_journal_merge_preserves_existing(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    target = tmp_path / "vault"
    ws.mkdir()
    _build_ws(ws, "NVDA")

    # Pre-existing journal from autoresearch side
    pre_journal = target / "companies/NVDA/journal.md"
    pre_journal.parent.mkdir(parents=True, exist_ok=True)
    pre_journal.write_text("## 2026-04-09 autoresearch entry\ncompile tick done\n")

    migrate_workspace(ws, target)

    text = pre_journal.read_text()
    assert "autoresearch entry" in text
    assert "coordinator log (migrated)" in text
    assert "tick 1" in text
