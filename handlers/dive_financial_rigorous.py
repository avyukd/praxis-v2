"""Financial rigorous dive — always-runs, emits INVESTABILITY line."""

from __future__ import annotations

from handlers import HandlerContext, HandlerResult
from handlers._dive_base import run_specialist_dive
from handlers.prompts.dive_financial_rigorous import SYSTEM_PROMPT


async def handle(ctx: HandlerContext) -> HandlerResult:
    return await run_specialist_dive(
        ctx,
        specialty_slug="financial-rigorous",
        specialty_label="Rigorous Financial Analysis",
        system_prompt=SYSTEM_PROMPT,
    )
