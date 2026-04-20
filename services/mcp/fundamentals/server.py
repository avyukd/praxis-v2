"""Fundamentals MCP server (D25) — yfinance-backed, Postgres-cached.

Runs over stdio. Registered in vault `.mcp-config.json` under the
`fundamentals` server name so its tools surface as
`mcp__fundamentals__<tool>`.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from praxis_core.logging import configure_logging, get_logger
from services.mcp.fundamentals import tools

log = get_logger("mcp.fundamentals.server")
mcp = FastMCP("praxis-fundamentals")


@mcp.tool()
async def company_overview(ticker: str) -> dict[str, Any]:
    """Company profile: name, sector, industry, marketCap, employees, summary."""
    return await tools.company_overview(ticker)


@mcp.tool()
async def list_financial_metrics(ticker: str, statement: str) -> list[str]:
    """List available metric names on a statement. statement ∈ {income, balance, cashflow}."""
    return await tools.list_financial_metrics(ticker, statement)


@mcp.tool()
async def get_financial_data(
    ticker: str,
    statement: str,
    metrics: list[str],
    period_type: str = "annual",
    count: int = 4,
) -> dict[str, Any]:
    """Fetch specific metrics from a statement. period_type ∈ {annual, quarterly}."""
    return await tools.get_financial_data(ticker, statement, metrics, period_type, count)


@mcp.tool()
async def get_full_statement(
    ticker: str, statement: str, period_type: str = "annual", count: int = 4
) -> dict[str, Any]:
    """Fetch a full statement (all metrics, N most recent periods)."""
    return await tools.get_full_statement(ticker, statement, period_type, count)


@mcp.tool()
async def get_earnings(ticker: str, count: int = 8) -> list[dict[str, Any]]:
    """Recent earnings dates + EPS estimate/reported/surprise."""
    return await tools.get_earnings(ticker, count)


@mcp.tool()
async def get_holders(ticker: str) -> dict[str, Any]:
    """Major, institutional, and mutual-fund holders (top 20 each)."""
    return await tools.get_holders(ticker)


@mcp.tool()
async def get_price(ticker: str) -> dict[str, Any]:
    """Current/delayed price + day high/low + 52w range."""
    return await tools.get_price(ticker)


@mcp.tool()
async def search_fundamentals(ticker: str, keyword: str) -> list[str]:
    """Search across all three statements' metric names for a keyword."""
    return await tools.search_fundamentals(ticker, keyword)


def main() -> None:
    configure_logging()
    log.info("mcp.fundamentals.start")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
