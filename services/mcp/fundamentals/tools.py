"""yfinance-backed tool implementations for the fundamentals MCP (D25).

Each tool fetches via yfinance through a Postgres read-through cache
(`fundamentals_cache`, 1h TTL). yfinance is sync, so we wrap calls in
`asyncio.to_thread` via `cache.with_cache`.
"""

from __future__ import annotations

from typing import Any

import yfinance as yf

from services.mcp.fundamentals.cache import with_cache

STATEMENT_ALIASES = {
    "income": "income_stmt",
    "income_statement": "income_stmt",
    "is": "income_stmt",
    "balance": "balance_sheet",
    "balance_sheet": "balance_sheet",
    "bs": "balance_sheet",
    "cashflow": "cashflow",
    "cash_flow": "cashflow",
    "cf": "cashflow",
}

PERIOD_FIELDS = {
    "annual": {
        "income_stmt": "income_stmt",
        "balance_sheet": "balance_sheet",
        "cashflow": "cashflow",
    },
    "quarterly": {
        "income_stmt": "quarterly_income_stmt",
        "balance_sheet": "quarterly_balance_sheet",
        "cashflow": "quarterly_cashflow",
    },
}


def _resolve_statement(statement: str) -> str:
    key = statement.lower().strip()
    if key not in STATEMENT_ALIASES:
        raise ValueError(f"unknown statement {statement!r} (try: income, balance, cashflow)")
    return STATEMENT_ALIASES[key]


def _resolve_period(period_type: str) -> str:
    p = period_type.lower().strip()
    if p not in ("annual", "quarterly"):
        raise ValueError(f"period_type must be 'annual' or 'quarterly', got {period_type!r}")
    return p


def _df_to_dict(df) -> dict[str, dict[str, float | None]]:
    """Convert yfinance DataFrame (metrics × periods) to {period_iso: {metric: value}}."""
    if df is None or df.empty:
        return {}
    out: dict[str, dict[str, float | None]] = {}
    for period in df.columns:
        period_key = period.isoformat() if hasattr(period, "isoformat") else str(period)
        out[period_key] = {}
        for metric in df.index:
            v = df.at[metric, period]
            try:
                out[period_key][str(metric)] = None if v is None else float(v)
            except (TypeError, ValueError):
                out[period_key][str(metric)] = None
    return out


async def company_overview(ticker: str) -> dict[str, Any]:
    """Key info: name, sector, industry, marketCap, fullTimeEmployees, longBusinessSummary."""

    def fetch() -> dict[str, Any]:
        info = yf.Ticker(ticker).info or {}
        keep = [
            "symbol",
            "shortName",
            "longName",
            "sector",
            "industry",
            "country",
            "marketCap",
            "enterpriseValue",
            "fullTimeEmployees",
            "longBusinessSummary",
            "website",
            "currency",
            "financialCurrency",
            "sharesOutstanding",
            "floatShares",
            "beta",
            "trailingPE",
            "forwardPE",
            "dividendYield",
        ]
        return {k: info.get(k) for k in keep}

    return await with_cache(ticker, "company_overview", {}, fetch)


async def list_financial_metrics(ticker: str, statement: str) -> list[str]:
    stmt = _resolve_statement(statement)

    def fetch() -> list[str]:
        t = yf.Ticker(ticker)
        df = getattr(t, stmt)
        if df is None or df.empty:
            return []
        return [str(m) for m in df.index]

    result = await with_cache(
        ticker, "list_financial_metrics", {"statement": stmt}, fetch
    )
    return result if isinstance(result, list) else []


async def get_financial_data(
    ticker: str,
    statement: str,
    metrics: list[str],
    period_type: str = "annual",
    count: int = 4,
) -> dict[str, Any]:
    stmt = _resolve_statement(statement)
    period = _resolve_period(period_type)
    field = PERIOD_FIELDS[period][stmt]

    def fetch() -> dict[str, Any]:
        t = yf.Ticker(ticker)
        df = getattr(t, field)
        if df is None or df.empty:
            return {"statement": stmt, "period_type": period, "metrics": {}}
        wanted = [m for m in metrics if m in df.index]
        missing = [m for m in metrics if m not in df.index]
        out: dict[str, Any] = {
            "statement": stmt,
            "period_type": period,
            "missing_metrics": missing,
            "metrics": {},
        }
        cols = list(df.columns)[:count]
        for m in wanted:
            out["metrics"][m] = {}
            for c in cols:
                v = df.at[m, c]
                try:
                    out["metrics"][m][c.isoformat() if hasattr(c, "isoformat") else str(c)] = (
                        None if v is None else float(v)
                    )
                except (TypeError, ValueError):
                    out["metrics"][m][str(c)] = None
        return out

    return await with_cache(
        ticker,
        "get_financial_data",
        {
            "statement": stmt,
            "metrics": sorted(metrics),
            "period_type": period,
            "count": count,
        },
        fetch,
    )


async def get_full_statement(
    ticker: str, statement: str, period_type: str = "annual", count: int = 4
) -> dict[str, Any]:
    stmt = _resolve_statement(statement)
    period = _resolve_period(period_type)
    field = PERIOD_FIELDS[period][stmt]

    def fetch() -> dict[str, Any]:
        t = yf.Ticker(ticker)
        df = getattr(t, field)
        if df is None or df.empty:
            return {"statement": stmt, "period_type": period, "data": {}}
        sliced = df.iloc[:, :count]
        return {
            "statement": stmt,
            "period_type": period,
            "data": _df_to_dict(sliced),
        }

    return await with_cache(
        ticker,
        "get_full_statement",
        {"statement": stmt, "period_type": period, "count": count},
        fetch,
    )


async def get_earnings(ticker: str, count: int = 8) -> list[dict[str, Any]]:
    def fetch() -> list[dict[str, Any]]:
        t = yf.Ticker(ticker)
        df = t.earnings_dates
        if df is None or df.empty:
            return []
        out: list[dict[str, Any]] = []
        for ts, row in df.head(count).iterrows():
            ts_iso = str(ts)
            item: dict[str, Any] = {"date": ts_iso}
            for col, v in row.items():
                try:
                    item[str(col)] = None if v is None else float(v)
                except (TypeError, ValueError):
                    item[str(col)] = str(v) if v is not None else None
            out.append(item)
        return out

    result = await with_cache(ticker, "get_earnings", {"count": count}, fetch)
    return result if isinstance(result, list) else []


async def get_holders(ticker: str) -> dict[str, Any]:
    def fetch() -> dict[str, Any]:
        t = yf.Ticker(ticker)
        out: dict[str, Any] = {}
        major = t.major_holders
        if major is not None and not major.empty:
            out["major"] = major.to_dict(orient="records") if hasattr(major, "to_dict") else []
        inst = t.institutional_holders
        if inst is not None and not inst.empty:
            out["institutional"] = inst.head(20).to_dict(orient="records")
        fund = t.mutualfund_holders
        if fund is not None and not fund.empty:
            out["mutual_fund"] = fund.head(20).to_dict(orient="records")
        return out

    return await with_cache(ticker, "get_holders", {}, fetch)


async def get_price(ticker: str) -> dict[str, Any]:
    def fetch() -> dict[str, Any]:
        info = yf.Ticker(ticker).info or {}
        return {
            "symbol": info.get("symbol"),
            "currentPrice": info.get("currentPrice") or info.get("regularMarketPrice"),
            "previousClose": info.get("previousClose"),
            "open": info.get("open"),
            "dayHigh": info.get("dayHigh"),
            "dayLow": info.get("dayLow"),
            "volume": info.get("volume"),
            "marketCap": info.get("marketCap"),
            "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh"),
            "fiftyTwoWeekLow": info.get("fiftyTwoWeekLow"),
            "currency": info.get("currency"),
        }

    # short TTL for prices
    from datetime import timedelta

    return await with_cache(ticker, "get_price", {}, fetch, ttl=timedelta(minutes=15))


async def search_fundamentals(ticker: str, keyword: str) -> list[str]:
    """Search across all three statements' metric names for a keyword (case-insensitive)."""
    kw = keyword.lower().strip()
    if not kw:
        return []

    def fetch() -> list[str]:
        t = yf.Ticker(ticker)
        hits: set[str] = set()
        for attr in ("income_stmt", "balance_sheet", "cashflow"):
            df = getattr(t, attr, None)
            if df is None or df.empty:
                continue
            for m in df.index:
                if kw in str(m).lower():
                    hits.add(f"{attr}:{m}")
        return sorted(hits)

    result = await with_cache(
        ticker, "search_fundamentals", {"keyword": kw}, fetch
    )
    return result if isinstance(result, list) else []
