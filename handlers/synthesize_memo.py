from __future__ import annotations

from sqlalchemy import select

from handlers import HandlerContext, HandlerResult
from handlers._common import SYSTEM_PROMPT_PREFIX, read_vault_schema, run_llm
from praxis_core.db.models import Investigation
from praxis_core.db.session import session_scope
from praxis_core.logging import get_logger
from praxis_core.schemas.payloads import SynthesizeMemoPayload
from praxis_core.schemas.task_types import TaskModel
from praxis_core.time_et import now_utc
from praxis_core.vault import conventions as vc

log = get_logger("handlers.synthesize_memo")


SYSTEM_PROMPT = (
    SYSTEM_PROMPT_PREFIX
    + """
Task: synthesize_memo

Produce a dated memo crystallizing the current state of research for this ticker.
Structure (required):
  frontmatter: type=memo, ticker, decision (Buy|Sell|Neutral|Too Hard), data_vintage, links
  ## Thesis                (1-2 sentence variant perception)
  ## What's new            (the catalyst that triggered this memo)
  ## Business overview
  ## Financial analysis    (tables with sourced numbers)
  ## Competitive position
  ## Valuation             (explicit assumptions)
  ## Variant perception    (3-col table: Market sees | We see | Why we're right)
  ## Risks                 (specific, kill-criteria-style)
  ## Confidence & gaps
  ## Related               (wikilinks, bidirectional)

Decision hygiene: "Too Hard" and "Neutral" are valid. Don't force conviction.

Memo path: <vault>/companies/<TICKER>/memos/<YYYY-MM-DD>-<memo_handle>.md
"""
)


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = SynthesizeMemoPayload.model_validate(ctx.payload)

    memo_path = vc.company_memo_path(ctx.vault_root, payload.ticker, payload.memo_handle)
    notes_path = vc.company_notes_path(ctx.vault_root, payload.ticker)
    thesis_path = vc.company_thesis_path(ctx.vault_root, payload.ticker)

    user_prompt = f"""SYNTHESIZE MEMO

Ticker: {payload.ticker}
Investigation: {payload.investigation_handle}
Thesis handle: {payload.thesis_handle or "(none)"}
Memo handle: {payload.memo_handle}

Inputs:
  - Company notes: {notes_path}
  - Company thesis (if exists): {thesis_path}
  - Investigation: <vault>/investigations/{payload.investigation_handle}.md

Write memo at: {memo_path}

Work from the existing notes/thesis/investigation context; do NOT run fresh ingestion.
If the notes are thin, the memo should be short and decisively Neutral or Too Hard.
"""

    schema = read_vault_schema(ctx.vault_root)
    system = SYSTEM_PROMPT + ("\n\n## Vault schema\n" + schema if schema else "")

    result = await run_llm(
        system_prompt=system,
        user_prompt=user_prompt,
        model=TaskModel.OPUS,
        max_turns=20,
        vault_root=ctx.vault_root,
    )

    # Mark investigation resolved regardless of LLM success — validator will catch incomplete work
    async def _update_investigation(s) -> None:
        inv = (
            await s.execute(
                select(Investigation).where(Investigation.handle == payload.investigation_handle)
            )
        ).scalar_one_or_none()
        if inv and result.finish_reason in ("stop", "max_turns"):
            inv.status = "resolved"
            inv.resolved_at = now_utc()  # DB field — UTC is the storage format
            existing = list(inv.artifacts or [])
            rel = str(memo_path.relative_to(ctx.vault_root))
            if rel not in existing:
                existing.append(rel)
            inv.artifacts = existing

    if payload.investigation_handle:
        if ctx.session is not None:
            await _update_investigation(ctx.session)
        else:
            async with session_scope() as session:
                await _update_investigation(session)

    log.info(
        "synthesize_memo.done",
        task_id=ctx.task_id,
        finish_reason=result.finish_reason,
        ticker=payload.ticker,
    )
    if result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=result, message="rate_limit")
    return HandlerResult(ok=True, llm_result=result)
