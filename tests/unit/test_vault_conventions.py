from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from praxis_core.time_et import ET
from praxis_core.vault import conventions as c


def test_company_paths(tmp_path: Path) -> None:
    assert c.company_dir(tmp_path, "NVDA") == tmp_path / "companies" / "NVDA"
    assert c.company_notes_path(tmp_path, "nvda") == tmp_path / "companies" / "NVDA" / "notes.md"
    assert c.company_thesis_path(tmp_path, "NVDA") == tmp_path / "companies" / "NVDA" / "thesis.md"
    assert (
        c.company_journal_path(tmp_path, "NVDA") == tmp_path / "companies" / "NVDA" / "journal.md"
    )


def test_ticker_validation() -> None:
    with pytest.raises(ValueError):
        c.company_notes_path(Path("/x"), "")
    with pytest.raises(ValueError):
        c.company_notes_path(Path("/x"), "123NVDA")
    # valid tickers
    c.company_notes_path(Path("/x"), "BRK.A")
    c.company_notes_path(Path("/x"), "BF-B")


def test_slugging(tmp_path: Path) -> None:
    p = c.theme_path(tmp_path, "AI Capex Digestion!!")
    assert p.name == "ai-capex-digestion.md"


def test_raw_filing_dir(tmp_path: Path) -> None:
    p = c.raw_filing_dir(tmp_path, "8-K", "0001045810-26-000047")
    assert p == tmp_path / "_raw" / "filings" / "8-k" / "0001045810-26-000047"


def test_analyzed_filing_dir(tmp_path: Path) -> None:
    p = c.analyzed_filing_dir(tmp_path, "8-K", "abc")
    assert p == tmp_path / "_analyzed" / "filings" / "8-k" / "abc"


def test_memo_paths(tmp_path: Path) -> None:
    # Use ET-aware datetime — all date conventions are in ET.
    dt = datetime(2026, 4, 18, 10, 0, tzinfo=ET)
    assert (
        c.company_memo_path(tmp_path, "NVDA", "AI Capex Thesis", dt).name
        == "2026-04-18-ai-capex-thesis.md"
    )
    assert (
        c.crosscut_memo_path(tmp_path, "Hormuz Scenarios", dt).name
        == "2026-04-18-hormuz-scenarios.md"
    )


def test_investigation_path(tmp_path: Path) -> None:
    p = c.investigation_path(tmp_path, "NVDA-AI-Capex-Digestion")
    assert p == tmp_path / "investigations" / "nvda-ai-capex-digestion.md"


def test_journal_daily(tmp_path: Path) -> None:
    dt = datetime(2026, 4, 18, 10, 0, tzinfo=ET)
    assert c.journal_daily_path(tmp_path, dt) == tmp_path / "journal" / "2026-04-18.md"
