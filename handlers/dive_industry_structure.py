"""Industry structure + cycle dive."""

from __future__ import annotations

from handlers import HandlerContext, HandlerResult
from handlers._dive_base import run_specialist_dive
from handlers.prompts.dive_industry_structure import SYSTEM_PROMPT


async def handle(ctx: HandlerContext) -> HandlerResult:
    return await run_specialist_dive(
        ctx,
        specialty_slug="industry-structure",
        specialty_label="Industry Structure & Cycle",
        system_prompt=SYSTEM_PROMPT,
    )
