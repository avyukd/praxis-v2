"""Business + moat dive (merge of old dive_business + dive_moat)."""

from __future__ import annotations

from handlers import HandlerContext, HandlerResult
from handlers._dive_base import run_specialist_dive
from handlers.prompts.dive_business_moat import SYSTEM_PROMPT


async def handle(ctx: HandlerContext) -> HandlerResult:
    return await run_specialist_dive(
        ctx,
        specialty_slug="business-moat",
        specialty_label="Business & Moat",
        system_prompt=SYSTEM_PROMPT,
    )
