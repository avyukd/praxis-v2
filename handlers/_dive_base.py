from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from praxis_core.logging import get_logger
from praxis_core.schemas.task_types import TaskModel
from praxis_core.vault import conventions as vc

from handlers import HandlerContext, HandlerResult
from handlers._common import SYSTEM_PROMPT_PREFIX, read_vault_schema, run_llm

log = get_logger("handlers.dive")


def build_dive_prompt(section: str, section_title: str) -> str:
    return (
        SYSTEM_PROMPT_PREFIX
        + f"""
Task: dive_{section}

You are the {section_title} specialist. Your output is a single section update to
<vault>/companies/<TICKER>/notes.md, plus a journal entry and an investigation log update.

Rigorous standards:
- Every quantitative claim must cite a source in <vault>/_raw/ or fundamentals MCP.
- "I don't know" > a guess. File a question in <vault>/questions/ if you hit a gap.
- Cross-link to relevant themes, concepts, people via [[wikilinks]].
- Update both ## Related sections bidirectionally.

Target section in notes.md: ## {section_title}
(If the section exists, append / refine; don't wipe prior content.)

Artifacts the validator checks:
  - companies/<TICKER>/notes.md exists and contains "## {section_title}"
  - companies/<TICKER>/journal.md has a new dated entry
"""
    )


async def run_dive(
    ctx: HandlerContext,
    *,
    section: str,
    section_title: str,
    focus_prompt: str,
) -> HandlerResult:
    ticker = ctx.payload.get("ticker")
    if not ticker:
        raise ValueError(f"{ctx.task_type} missing ticker")
    investigation_handle = ctx.payload.get("investigation_handle") or ""
    now_iso = datetime.now(timezone.utc).isoformat()

    notes_path = vc.company_notes_path(ctx.vault_root, ticker)
    journal_path = vc.company_journal_path(ctx.vault_root, ticker)
    inv_path = (
        vc.investigation_path(ctx.vault_root, investigation_handle)
        if investigation_handle
        else None
    )

    user_prompt = f"""DIVE: {section_title}

Ticker: {ticker}
Investigation: {investigation_handle or "(standalone)"}

Notes file: {notes_path}
Journal file: {journal_path}
Investigation file: {inv_path or "(none)"}

{focus_prompt}

Process:
1. Read {notes_path} if it exists. Read any related _analyzed/ files for this ticker.
2. Read _raw/ filings for this ticker if relevant.
3. Update the ## {section_title} section of {notes_path} with a rigorous, sourced treatment.
4. Append to {journal_path}: `- {now_iso}: dive_{section} advance ({investigation_handle})`
5. If investigation file exists at {inv_path}, append to its ## Log: `- {now_iso}: dive_{section} completed`.
"""

    schema = read_vault_schema(ctx.vault_root)
    system = build_dive_prompt(section, section_title) + (
        "\n\n## Vault schema\n" + schema if schema else ""
    )

    result = await run_llm(
        system_prompt=system,
        user_prompt=user_prompt,
        model=TaskModel.OPUS,
        max_turns=20,
        vault_root=ctx.vault_root,
    )
    log.info(
        "dive.done",
        section=section,
        task_id=ctx.task_id,
        finish_reason=result.finish_reason,
        ticker=ticker,
    )
    if result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=result, message="rate_limit")
    return HandlerResult(ok=True, llm_result=result)
