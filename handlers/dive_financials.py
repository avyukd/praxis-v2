from __future__ import annotations

from handlers import HandlerContext, HandlerResult
from handlers._dive_base import run_dive

FOCUS = """
Focus: Financials
- 5-year revenue trajectory: growth rate, seasonality, one-time items
- Margin structure and evolution: gross → operating → EBITDA → FCF
- Capital intensity and ROIC
- Balance sheet: debt maturity wall, covenants, liquidity
- Working capital, cash conversion cycle
- Capital returns: buybacks, dividends, M&A history
- Red flags: DSO creep, inventory build, restatements, goodwill impairment

Extract key numbers into <vault>/companies/<TICKER>/data/financials.json if not already there.

Every number cited with source — either a filing in _raw/ or a `[fundamentals: get_financial_data(...)]`
annotation. No vague "margins are strong" claims.
"""


async def handle(ctx: HandlerContext) -> HandlerResult:
    return await run_dive(ctx, section="financials", section_title="Financials", focus_prompt=FOCUS)
