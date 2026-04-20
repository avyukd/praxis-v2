"""Shared dive runner — one Sonnet/Opus call producing a dives/<specialty>.md file.

Per Section B D23: handlers write to companies/<TICKER>/dives/<specialty>.md
as full standalone files (no more section-append to notes.md). Each dive is
self-contained; synthesize_memo reads them all later.

Dive-quality refactor: inject the ResearchBudget corresponding to the
investigation's research_priority into the user prompt. Budget carries the
word cap, web-lookup cap, and depth label that the validator + LLM prompt
both respect. Priority also scales the LLM dollar budget — a Full Deep Dive
gets up to $8, a Quick Screen caps at $1.
"""

from __future__ import annotations

from pathlib import Path

from handlers import HandlerContext, HandlerResult
from handlers._common import DIVE_ALLOWED_TOOLS, read_vault_schema, run_llm
from praxis_core.logging import get_logger
from praxis_core.research.budget import ResearchBudget
from praxis_core.schemas.task_types import TaskModel
from praxis_core.tasks.investigations import touch_investigation
from praxis_core.time_et import et_iso
from praxis_core.vault import conventions as vc

log = get_logger("handlers.dive")

# Priority tier -> hard ceiling on LLM spend for a single dive. Nested inside
# the CLI invoker's max_budget_usd so it can't exceed this regardless of
# how long the specialist takes.
_PRIORITY_USD_BUDGET: dict[str, float] = {
    "minimal": 1.00,
    "conservative": 2.00,
    "standard": 4.00,
    "thorough": 6.00,
    "maximum": 8.00,
}


def dive_output_path(vault_root: Path, ticker: str, specialty_slug: str) -> Path:
    """companies/<TICKER>/dives/<specialty>.md — the D53 convention."""
    return vc.company_dir(vault_root, ticker) / "dives" / f"{specialty_slug}.md"


def _priority_from_ctx(ctx: HandlerContext) -> int:
    """Priority lives on the payload (propagated from the investigation via
    orchestrate_dive). Falls back to 5 (standard research) if missing."""
    p = (ctx.payload or {}).get("research_priority")
    try:
        return int(p) if p is not None else 5
    except (TypeError, ValueError):
        return 5


async def run_specialist_dive(
    ctx: HandlerContext,
    *,
    specialty_slug: str,
    specialty_label: str,
    system_prompt: str,
    focus: str = "",
    model: TaskModel = TaskModel.OPUS,
    max_budget_usd: float | None = None,
) -> HandlerResult:
    """Execute a specialist dive: compose user prompt, call LLM, let it write
    the output file via the Write tool. Caller (validator) checks artifact
    after."""
    ticker = ctx.payload.get("ticker")
    if not ticker:
        raise ValueError(f"{ctx.task_type} missing ticker")
    investigation_handle = ctx.payload.get("investigation_handle") or ""
    investigation_id = ctx.payload.get("investigation_id")

    priority = _priority_from_ctx(ctx)
    budget = ResearchBudget.from_priority(priority)
    usd_budget = max_budget_usd if max_budget_usd is not None else _PRIORITY_USD_BUDGET.get(
        budget.agent_policy, 4.00
    )

    output_path = dive_output_path(ctx.vault_root, ticker, specialty_slug)
    notes_path = vc.company_notes_path(ctx.vault_root, ticker)
    inv_path = (
        vc.investigation_path(ctx.vault_root, investigation_handle)
        if investigation_handle
        else None
    )

    user_prompt = f"""DIVE: {specialty_label}

Ticker: {ticker}
Investigation: {investigation_handle or "(standalone)"}
Specialty: {specialty_slug}
Research depth: **{budget.depth_label}**

Research budget (respect these limits):
  - Word cap for this dive: ~{budget.specialist_words} words
  - Web lookups allowed: {budget.web_lookups}
  - Tool-call policy: {budget.agent_policy}

Output file (you MUST write this file atomically via the Write tool):
  {output_path}

Context sources (read what's useful — and the vault is often thin, which
is why you have web access):
  - {notes_path} (compiled notes — may or may not exist)
  - _analyzed/ directory for this ticker's recent filings / press releases
  - _raw/ directory for raw source material
  - Vault themes/, concepts/ for cross-cutting knowledge
  - Investigation file: {inv_path or "(none)"}

{focus}

Process (non-negotiable — the validator checks for proof of each step):

1. **Retrieve first.** Call the fundamentals MCP (at least 3 distinct tool
   calls) and fetch primary sources via WebFetch / WebSearch /
   Bash(curl:*) as needed. A dive that produces a "data-limited" verdict
   without retrieval evidence FAILS VALIDATION.

2. **Analyze second.** Produce {output_path} with the structure defined
   in the system prompt. Honor the word cap — better to compress than to
   sprawl.

3. **Document your sources.** End the file with a `## Sources consulted`
   section listing every tool call and its useful output. This is how the
   validator verifies research depth.

4. **Write atomically via the Write tool.** Do not overwrite existing
   sibling dives in companies/<TICKER>/dives/ (other specialists own those).

5. **Log in the investigation file** (if it exists): append one line to
   its `## Log` section:
   `- {et_iso()}: {specialty_slug} completed ({budget.depth_label})`
"""

    schema = read_vault_schema(ctx.vault_root)
    system = system_prompt + ("\n\n## Vault schema\n" + schema if schema else "")

    result = await run_llm(
        system_prompt=system,
        user_prompt=user_prompt,
        model=model,
        max_budget_usd=usd_budget,
        vault_root=ctx.vault_root,
        allowed_tools=DIVE_ALLOWED_TOOLS,
    )

    if investigation_id and ctx.session is not None:
        try:
            await touch_investigation(ctx.session, investigation_id)
        except Exception as e:
            log.warning("dive.touch_fail", error=str(e))

    log.info(
        "dive.done",
        specialty=specialty_slug,
        task_id=ctx.task_id,
        finish_reason=result.finish_reason,
        ticker=ticker,
        priority=priority,
        depth=budget.depth_label,
    )
    if result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=result, message="rate_limit")
    return HandlerResult(ok=True, llm_result=result)
