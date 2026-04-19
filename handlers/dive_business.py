from __future__ import annotations

from handlers import HandlerContext, HandlerResult
from handlers._dive_base import run_dive

FOCUS = """
Focus: Business
- What does this company actually sell and to whom?
- Segments with revenue share and growth trajectory
- Customer concentration, pricing model, contract economics
- Distribution channels, geographic mix
- Unit economics at the product/segment level where possible
- Material changes in business mix in the past 2 years

Avoid: generic "X operates in Y industry" boilerplate. Specifics or nothing.
"""


async def handle(ctx: HandlerContext) -> HandlerResult:
    return await run_dive(ctx, section="business", section_title="Business", focus_prompt=FOCUS)
