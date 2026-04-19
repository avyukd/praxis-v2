"""Tests that the EDGAR poller correctly applies filters and routes filings.

Tests the `_decide_ingest()` function in isolation (no network, no yfinance) using a
stub cik_map and monkey-patched market_cap lookup.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from services.pollers.edgar_8k import EdgarFiling, _decide_ingest


class _StubCikMap:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._m = mapping

    def lookup(self, cik: str) -> str | None:
        return self._m.get(str(cik).zfill(10))


def _filing(
    accession: str = "0001-26-000001",
    form: str = "8-K",
    cik: str = "0000001234",
    items: list[str] | None = None,
    ticker: str | None = None,
) -> EdgarFiling:
    return EdgarFiling(
        accession=accession,
        form_type=form,
        cik=cik,
        title=f"{form} - TEST Corp",
        link="https://sec.gov/x",
        published=datetime(2026, 4, 18),
        ticker=ticker,
        items=items or [],
    )


@pytest.mark.asyncio
async def test_rejects_8k_with_no_matching_items() -> None:
    filing = _filing(items=["3.01", "9.01"])  # Delisting + exhibits only
    with patch("services.pollers.edgar_8k.fetch_market_cap_usd"):
        decision = await _decide_ingest(filing, session=AsyncMock(), cik_map=_StubCikMap({}))
    assert decision.accept is False
    assert "not in allowlist" in decision.reason


@pytest.mark.asyncio
async def test_accepts_8k_with_material_agreement_and_small_mcap() -> None:
    filing = _filing(items=["1.01", "9.01"], cik="0000001234")
    cik_map = _StubCikMap({"0000001234": "SMALL"})
    with patch(
        "services.pollers.edgar_8k.fetch_market_cap_usd",
        new=AsyncMock(return_value=type("L", (), {"market_cap_usd": 500_000_000})()),
    ):
        decision = await _decide_ingest(filing, session=AsyncMock(), cik_map=cik_map)
    assert decision.accept is True
    assert decision.ticker == "SMALL"
    assert decision.market_cap_usd == 500_000_000
    assert "1.01" in decision.matched_items


@pytest.mark.asyncio
async def test_rejects_large_cap_even_with_good_items() -> None:
    filing = _filing(items=["2.02"], cik="0000001234")  # Earnings
    cik_map = _StubCikMap({"0000001234": "BIG"})
    with patch(
        "services.pollers.edgar_8k.fetch_market_cap_usd",
        new=AsyncMock(return_value=type("L", (), {"market_cap_usd": 50_000_000_000})()),
    ):
        decision = await _decide_ingest(filing, session=AsyncMock(), cik_map=cik_map)
    assert decision.accept is False
    assert "mcap" in decision.reason
    assert decision.ticker == "BIG"


@pytest.mark.asyncio
async def test_accepts_unknown_ticker_small_presumed() -> None:
    """Ticker not in CIK map → no mcap lookup → pass (preserve micro-cap coverage)."""
    filing = _filing(items=["5.02"], cik="9999999999")  # Officer departure
    cik_map = _StubCikMap({})  # empty map
    with patch("services.pollers.edgar_8k.fetch_market_cap_usd") as mock_fetch:
        decision = await _decide_ingest(filing, session=AsyncMock(), cik_map=cik_map)
    assert decision.accept is True
    assert decision.ticker is None
    # market_cap lookup should never have been called (no ticker to query)
    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_non_8k_skips_item_filter() -> None:
    """10-Q, 10-K, etc. don't have item codes — they should pass item filter."""
    filing = _filing(form="10-Q", items=[], cik="0000001234")
    cik_map = _StubCikMap({"0000001234": "SMALL"})
    with patch(
        "services.pollers.edgar_8k.fetch_market_cap_usd",
        new=AsyncMock(return_value=type("L", (), {"market_cap_usd": 100_000_000})()),
    ):
        decision = await _decide_ingest(filing, session=AsyncMock(), cik_map=cik_map)
    assert decision.accept is True


@pytest.mark.asyncio
async def test_accepts_unknown_mcap_from_yfinance() -> None:
    """Ticker known but yfinance returns None — keep it (small obscure name)."""
    filing = _filing(items=["1.01"], cik="0000001234")
    cik_map = _StubCikMap({"0000001234": "OBSCURE"})
    with patch(
        "services.pollers.edgar_8k.fetch_market_cap_usd",
        new=AsyncMock(return_value=type("L", (), {"market_cap_usd": None})()),
    ):
        decision = await _decide_ingest(filing, session=AsyncMock(), cik_map=cik_map)
    assert decision.accept is True
    assert decision.market_cap_usd is None
