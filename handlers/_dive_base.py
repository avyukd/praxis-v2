"""Shared dive runner — one Sonnet/Opus call producing a dives/<specialty>.md file.

Per Section B D23: handlers write to companies/<TICKER>/dives/<specialty>.md
as full standalone files (no more section-append to notes.md). Each dive is
self-contained; synthesize_memo reads them all later.
"""

from __future__ import annotations

from pathlib import Path

from handlers import HandlerContext, HandlerResult
from handlers._common import read_vault_schema, run_llm
from praxis_core.logging import get_logger
from praxis_core.schemas.task_types import TaskModel
from praxis_core.tasks.investigations import touch_investigation
from praxis_core.time_et import et_iso
from praxis_core.vault import conventions as vc

log = get_logger("handlers.dive")


def dive_output_path(vault_root: Path, ticker: str, specialty_slug: str) -> Path:
    """companies/<TICKER>/dives/<specialty>.md — the D53 convention."""
    return vc.company_dir(vault_root, ticker) / "dives" / f"{specialty_slug}.md"


async def run_specialist_dive(
    ctx: HandlerContext,
    *,
    specialty_slug: str,
    specialty_label: str,
    system_prompt: str,
    focus: str = "",
    model: TaskModel = TaskModel.OPUS,
    max_budget_usd: float = 5.00,
) -> HandlerResult:
    """Execute a specialist dive: compose user prompt, call LLM, let it write
    the output file via the Write tool. Caller (validator) checks artifact
    after."""
    ticker = ctx.payload.get("ticker")
    if not ticker:
        raise ValueError(f"{ctx.task_type} missing ticker")
    investigation_handle = ctx.payload.get("investigation_handle") or ""
    investigation_id = ctx.payload.get("investigation_id")

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

Output file: {output_path}
Context sources (read what's useful):
  - {notes_path} (compiled notes — likely exists)
  - _analyzed/ directory for this ticker's recent filings/PRs
  - _raw/ directory for raw source material
  - Vault themes/, concepts/ for cross-cutting knowledge
  - Investigation file: {inv_path or "(none)"}

{focus}

Process:
1. Read the relevant context sources (be selective — don't read everything).
2. Produce {output_path} with the structure defined in the system prompt.
3. Write atomically via the Write tool — do not overwrite existing sibling
   dives in companies/<TICKER>/dives/ (other specialists own those).
4. If investigation file exists, append one line to its ## Log section:
   `- {et_iso()}: {specialty_slug} completed`
"""

    schema = read_vault_schema(ctx.vault_root)
    system = system_prompt + ("\n\n## Vault schema\n" + schema if schema else "")

    result = await run_llm(
        system_prompt=system,
        user_prompt=user_prompt,
        model=model,
        max_budget_usd=max_budget_usd,
        vault_root=ctx.vault_root,
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
    )
    if result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=result, message="rate_limit")
    return HandlerResult(ok=True, llm_result=result)
