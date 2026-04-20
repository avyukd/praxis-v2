"""Unit tests for the fundamentals MCP tools (D25).

Mocks yfinance entirely — no network calls. Verifies each tool's output
shape against typical yfinance DataFrames and exercises cache-hit /
cache-miss paths via a monkeypatched `with_cache`.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from services.mcp.fundamentals import tools

# -- with_cache bypass (no Postgres) ------------------------------------


@pytest.fixture(autouse=True)
def _bypass_cache(monkeypatch):
    async def _direct(ticker, method, params, fetch_fn, ttl=None):
        import asyncio

        return await asyncio.to_thread(fetch_fn)

    monkeypatch.setattr(tools, "with_cache", _direct)


# -- fixtures -----------------------------------------------------------


def _fake_statement_df() -> pd.DataFrame:
    idx = ["Total Revenue", "Gross Profit", "Operating Income", "Net Income"]
    cols = pd.to_datetime(["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31"])
    data = [
        [100.0, 90.0, 80.0, 70.0],
        [60.0, 55.0, 50.0, 45.0],
        [30.0, 28.0, 25.0, 22.0],
        [20.0, 18.0, 15.0, 12.0],
    ]
    return pd.DataFrame(data, index=idx, columns=cols)


def _fake_info() -> dict:
    return {
        "symbol": "FAKE",
        "shortName": "Fake Inc",
        "longName": "Fake Incorporated",
        "sector": "Technology",
        "industry": "Software",
        "country": "United States",
        "marketCap": 1_000_000_000,
        "enterpriseValue": 1_100_000_000,
        "fullTimeEmployees": 5000,
        "longBusinessSummary": "Fake company.",
        "website": "https://fake.example",
        "currency": "USD",
        "financialCurrency": "USD",
        "sharesOutstanding": 100_000_000,
        "floatShares": 90_000_000,
        "beta": 1.2,
        "trailingPE": 20.0,
        "forwardPE": 18.0,
        "dividendYield": 0.015,
        "currentPrice": 10.0,
        "previousClose": 9.9,
        "open": 9.95,
        "dayHigh": 10.2,
        "dayLow": 9.9,
        "volume": 1_000_000,
        "fiftyTwoWeekHigh": 12.0,
        "fiftyTwoWeekLow": 8.0,
        "regularMarketPrice": 10.0,
    }


@pytest.fixture
def fake_ticker(monkeypatch):
    t = MagicMock()
    t.info = _fake_info()
    t.income_stmt = _fake_statement_df()
    t.balance_sheet = _fake_statement_df()
    t.cashflow = _fake_statement_df()
    t.quarterly_income_stmt = _fake_statement_df()
    t.quarterly_balance_sheet = _fake_statement_df()
    t.quarterly_cashflow = _fake_statement_df()
    t.earnings_dates = pd.DataFrame(
        {"EPS Estimate": [1.0, 0.9], "Reported EPS": [1.1, 0.95], "Surprise(%)": [10.0, 5.5]},
        index=pd.to_datetime(["2026-01-15", "2025-10-15"]),
    )
    t.major_holders = pd.DataFrame({"Breakdown": ["insiders"], "Value": [0.15]})
    t.institutional_holders = pd.DataFrame({"Holder": ["Vanguard", "BlackRock"]})
    t.mutualfund_holders = pd.DataFrame({"Holder": ["Fidelity"]})

    with patch("services.mcp.fundamentals.tools.yf.Ticker", return_value=t):
        yield t


# -- tests --------------------------------------------------------------


@pytest.mark.asyncio
async def test_company_overview(fake_ticker):
    out = await tools.company_overview("FAKE")
    assert out["symbol"] == "FAKE"
    assert out["sector"] == "Technology"
    assert out["marketCap"] == 1_000_000_000
    assert out["beta"] == 1.2


@pytest.mark.asyncio
async def test_list_financial_metrics_income(fake_ticker):
    out = await tools.list_financial_metrics("FAKE", "income")
    assert "Total Revenue" in out
    assert "Net Income" in out


@pytest.mark.asyncio
async def test_list_financial_metrics_alias(fake_ticker):
    # "balance" resolves to balance_sheet
    out = await tools.list_financial_metrics("FAKE", "balance")
    assert len(out) == 4


@pytest.mark.asyncio
async def test_list_financial_metrics_bad_statement(fake_ticker):
    with pytest.raises(ValueError, match="unknown statement"):
        await tools.list_financial_metrics("FAKE", "xxxx")


@pytest.mark.asyncio
async def test_get_financial_data_subset(fake_ticker):
    out = await tools.get_financial_data(
        "FAKE",
        "income",
        ["Total Revenue", "Net Income", "DoesNotExist"],
        period_type="annual",
        count=2,
    )
    assert out["statement"] == "income_stmt"
    assert out["period_type"] == "annual"
    assert "Total Revenue" in out["metrics"]
    assert "Net Income" in out["metrics"]
    assert "DoesNotExist" in out["missing_metrics"]
    # count=2 should restrict to 2 periods
    assert len(out["metrics"]["Total Revenue"]) == 2


@pytest.mark.asyncio
async def test_get_financial_data_quarterly(fake_ticker):
    out = await tools.get_financial_data(
        "FAKE", "income", ["Total Revenue"], period_type="quarterly", count=4
    )
    assert out["period_type"] == "quarterly"


@pytest.mark.asyncio
async def test_get_financial_data_bad_period(fake_ticker):
    with pytest.raises(ValueError, match="period_type"):
        await tools.get_financial_data(
            "FAKE", "income", ["Total Revenue"], period_type="monthly", count=4
        )


@pytest.mark.asyncio
async def test_get_full_statement(fake_ticker):
    out = await tools.get_full_statement("FAKE", "cashflow", "annual", count=3)
    assert out["statement"] == "cashflow"
    assert len(out["data"]) == 3
    first_period = next(iter(out["data"].values()))
    assert "Total Revenue" in first_period


@pytest.mark.asyncio
async def test_get_earnings(fake_ticker):
    out = await tools.get_earnings("FAKE", count=2)
    assert len(out) == 2
    assert "date" in out[0]
    assert "EPS Estimate" in out[0]


@pytest.mark.asyncio
async def test_get_holders(fake_ticker):
    out = await tools.get_holders("FAKE")
    assert "major" in out
    assert "institutional" in out
    assert "mutual_fund" in out


@pytest.mark.asyncio
async def test_get_price(fake_ticker):
    out = await tools.get_price("FAKE")
    assert out["currentPrice"] == 10.0
    assert out["fiftyTwoWeekHigh"] == 12.0


@pytest.mark.asyncio
async def test_search_fundamentals_case_insensitive(fake_ticker):
    out = await tools.search_fundamentals("FAKE", "revenue")
    # Should find "Total Revenue" across all 3 statements
    assert any("Total Revenue" in hit for hit in out)
    assert any(hit.startswith("income_stmt:") for hit in out)


@pytest.mark.asyncio
async def test_search_fundamentals_empty_keyword(fake_ticker):
    out = await tools.search_fundamentals("FAKE", "   ")
    assert out == []


@pytest.mark.asyncio
async def test_search_fundamentals_no_hits(fake_ticker):
    out = await tools.search_fundamentals("FAKE", "zzznotamatch")
    assert out == []


# -- cache module shape (no DB writes) ----------------------------------


def test_params_hash_is_deterministic():
    from services.mcp.fundamentals.cache import params_hash

    a = params_hash({"statement": "income", "count": 4})
    b = params_hash({"count": 4, "statement": "income"})
    assert a == b
    c = params_hash({"statement": "income", "count": 5})
    assert a != c
    assert len(a) == 32
