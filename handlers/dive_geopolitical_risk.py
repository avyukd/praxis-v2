"""Geopolitical risk dive — often skipped by orchestrator."""

from __future__ import annotations

from handlers import HandlerContext, HandlerResult
from handlers._dive_base import run_specialist_dive
from handlers.prompts.dive_geopolitical_risk import SYSTEM_PROMPT


async def handle(ctx: HandlerContext) -> HandlerResult:
    return await run_specialist_dive(
        ctx,
        specialty_slug="geopolitical-risk",
        specialty_label="Geopolitical Risk",
        system_prompt=SYSTEM_PROMPT,
    )
