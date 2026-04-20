"""Macro dive — often skipped when existing themes already cover."""

from __future__ import annotations

from handlers import HandlerContext, HandlerResult
from handlers._dive_base import run_specialist_dive
from handlers.prompts.dive_macro import SYSTEM_PROMPT


async def handle(ctx: HandlerContext) -> HandlerResult:
    return await run_specialist_dive(
        ctx,
        specialty_slug="macro",
        specialty_label="Macro",
        system_prompt=SYSTEM_PROMPT,
    )
