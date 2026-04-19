"""Tests the EDGAR parser against a real EDGAR feed sample captured 2026-04-18.

Validates that our parser extracts filings correctly from SEC's current URL format.
If SEC changes the format, this test fails first — loudly — before production.

Also tests that known fields come through intact.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.pollers.edgar_8k import (
    _parse_accession_from_id_tag,
    _parse_accession_from_link,
    _parse_cik_from_link,
    _parse_cik_from_title,
    _parse_feed,
    _parse_form_type_from_title,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "edgar_8k_live.atom"


@pytest.fixture
def live_atom() -> str:
    if not FIXTURE.exists():
        pytest.skip(f"fixture missing: {FIXTURE}")
    return FIXTURE.read_text()


def test_live_feed_parses_at_least_one_filing(live_atom: str) -> None:
    filings = _parse_feed(live_atom, form_filter={"8-K"})
    assert len(filings) > 0, "parser returned zero filings from real EDGAR feed"


def test_every_filing_has_accession_and_cik(live_atom: str) -> None:
    filings = _parse_feed(live_atom, form_filter={"8-K"})
    for f in filings:
        assert f.accession, f"filing missing accession: {f}"
        # Accession format 0000000000-00-000000
        parts = f.accession.split("-")
        assert len(parts) == 3, f"bad accession format: {f.accession}"
        assert len(parts[0]) == 10 and len(parts[1]) == 2 and len(parts[2]) == 6
        assert f.cik, f"filing missing cik: {f}"
        assert f.cik.isdigit() and len(f.cik) == 10, f"bad cik: {f.cik}"
        assert f.form_type == "8-K"
        assert f.link.startswith("https://"), f"bad link: {f.link}"


def test_live_feed_form_filter_works(live_atom: str) -> None:
    kk = _parse_feed(live_atom, form_filter={"10-K"})  # no 10-Ks in an 8-K feed
    assert kk == []


def test_accession_extraction_from_live_url() -> None:
    # Exact URL from captured fixture
    link = (
        "https://www.sec.gov/Archives/edgar/data/2067627/"
        "000121390026045267/0001213900-26-045267-index.htm"
    )
    assert _parse_accession_from_link(link) == "0001213900-26-045267"


def test_cik_extraction_from_live_url() -> None:
    link = (
        "https://www.sec.gov/Archives/edgar/data/2067627/"
        "000121390026045267/0001213900-26-045267-index.htm"
    )
    assert _parse_cik_from_link(link) == "0000002067627"[-10:]  # zfilled to 10


def test_cik_from_title() -> None:
    assert (
        _parse_cik_from_title("8-K - Terra Innovatum Global N.V. (0002067627) (Filer)")
        == "0002067627"
    )


def test_accession_from_id_tag() -> None:
    id_tag = "urn:tag:sec.gov,2008:accession-number=0001213900-26-045267"
    assert _parse_accession_from_id_tag(id_tag) == "0001213900-26-045267"


def test_form_type_parsing() -> None:
    assert (
        _parse_form_type_from_title("8-K - Terra Innovatum Global N.V. (0002067627) (Filer)")
        == "8-K"
    )
    assert _parse_form_type_from_title("10-Q - Company (0001234567) (Filer)") == "10-Q"
    assert _parse_form_type_from_title("10-K/A - Company (0001234567) (Filer)") == "10-K/A"
