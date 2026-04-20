"""Live yfinance smoke — only runs when PRAXIS_TEST_LIVE_YF=1 is set."""

from __future__ import annotations

import os

import pytest

from services.mcp.fundamentals import tools

pytestmark = pytest.mark.skipif(
    os.environ.get("PRAXIS_TEST_LIVE_YF") != "1",
    reason="set PRAXIS_TEST_LIVE_YF=1 to run against real yfinance",
)


@pytest.fixture(autouse=True)
def _bypass_cache(monkeypatch):
    async def _direct(ticker, method, params, fetch_fn, ttl=None):
        import asyncio

        return await asyncio.to_thread(fetch_fn)

    monkeypatch.setattr(tools, "with_cache", _direct)


@pytest.mark.asyncio
async def test_aapl_overview_live():
    out = await tools.company_overview("AAPL")
    assert out.get("symbol") == "AAPL"
    assert out.get("marketCap") and out["marketCap"] > 1_000_000_000


@pytest.mark.asyncio
async def test_aapl_financial_metrics_live():
    out = await tools.list_financial_metrics("AAPL", "income")
    assert len(out) > 5
    # Expect at least one revenue-like metric
    assert any("revenue" in m.lower() or "sales" in m.lower() for m in out)


@pytest.mark.asyncio
async def test_aapl_price_live():
    out = await tools.get_price("AAPL")
    assert out.get("currentPrice") is not None
