"""Custom specialist dive — orchestrator-generated prompt (D23)."""

from __future__ import annotations

import re

from handlers import HandlerContext, HandlerResult
from handlers._dive_base import run_specialist_dive
from handlers.prompts.dive_custom import SYSTEM_PROMPT_TEMPLATE


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", text.lower()).strip("-")
    return slug or "custom"


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = ctx.payload
    specialty = payload.get("specialty") or "custom-analyst"
    why = payload.get("why") or "(not specified)"
    focus = payload.get("focus") or "Apply rigor appropriate to this question."

    specialty_slug = _slugify(specialty)

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        specialty=specialty,
        why=why,
        focus=focus,
        specialty_slug=specialty_slug,
    )

    return await run_specialist_dive(
        ctx,
        specialty_slug=specialty_slug,
        specialty_label=f"Custom: {specialty}",
        system_prompt=system_prompt,
        focus=f"Focus: {focus}",
    )
