"""Unit tests for the search-index EDGAR poller path."""

from __future__ import annotations

from services.pollers.edgar_8k import _build_filing_from_hit, _parse_ticker_from_display


def _hit(**src_overrides) -> dict:
    base = {
        "_source": {
            "ciks": ["0001045810"],
            "adsh": "0001045810-26-000047",
            "form": "8-K",
            "file_date": "2026-04-20",
            "display_names": ["NVIDIA CORP  (NVDA)  (CIK 0001045810)"],
            "items": ["2.02", "9.01"],
        }
    }
    base["_source"].update(src_overrides)
    return base


def test_parse_ticker_single():
    assert _parse_ticker_from_display("NVIDIA CORP  (NVDA)  (CIK 0001045810)") == "NVDA"


def test_parse_ticker_multi_class_takes_first():
    # AITX has two share classes — take the first
    assert (
        _parse_ticker_from_display("Artificial Intelligence Technology Solutions Inc.  (AITX, AITXD)  (CIK 0001498148)")
        == "AITX"
    )


def test_parse_ticker_no_ticker_returns_none():
    # Some filers don't have a ticker column populated
    assert _parse_ticker_from_display("AMERICAN AIRLINES, INC.  (CIK 0000004515)") is None


def test_parse_ticker_with_dot():
    assert _parse_ticker_from_display("Brookfield  (BAM.A)  (CIK 0001234567)") == "BAM.A"


def test_build_filing_happy_path():
    f = _build_filing_from_hit(_hit())
    assert f is not None
    assert f.accession == "0001045810-26-000047"
    assert f.form_type == "8-K"
    assert f.cik == "0001045810"
    assert f.ticker == "NVDA"
    assert f.items == ["2.02", "9.01"]
    assert f.link.startswith(
        "https://www.sec.gov/Archives/edgar/data/1045810/00010458102600004"
    )
    assert f.link.endswith("/0001045810-26-000047-index.htm")


def test_build_filing_rejects_non_8k():
    f = _build_filing_from_hit(_hit(form="10-Q"))
    assert f is None


def test_build_filing_rejects_missing_adsh():
    f = _build_filing_from_hit({"_source": {"form": "8-K"}})
    assert f is None


def test_build_filing_handles_missing_ticker():
    f = _build_filing_from_hit(
        _hit(display_names=["AMERICAN AIRLINES, INC.  (CIK 0000004515)"])
    )
    assert f is not None
    assert f.ticker is None


def test_build_filing_8k_a_accepted():
    # 8-K/A (amendment) should still pass because form startswith "8-K"
    f = _build_filing_from_hit(_hit(form="8-K/A"))
    assert f is not None
    assert f.form_type == "8-K/A"


def test_build_filing_handles_empty_items():
    f = _build_filing_from_hit(_hit(items=[]))
    assert f is not None
    assert f.items == []
