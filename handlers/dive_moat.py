from __future__ import annotations

from handlers import HandlerContext, HandlerResult
from handlers._dive_base import run_dive


FOCUS = """
Focus: Moat
- What is the source of durable competitive advantage? (intangibles, switching costs,
  network effect, cost advantage, efficient scale — Morningstar framework)
- Evidence for the moat — pricing power, retention, gross margin stability, market share
- Threats to the moat: disruption vectors, incumbent countermeasures, regulatory exposure
- Quantify durability: 10-year durability view

Be adversarial. Where would a skeptical analyst attack the moat thesis?
"""


async def handle(ctx: HandlerContext) -> HandlerResult:
    return await run_dive(ctx, section="moat", section_title="Moat", focus_prompt=FOCUS)
