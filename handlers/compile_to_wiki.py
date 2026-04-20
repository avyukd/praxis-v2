"""compile_to_wiki handler — wires into the trade_relevant path from analyze_filing.

Section D D37-D43:
  - Pre-write backup of notes.md (D38 — 25% shrink-guard lives in validator)
  - Don't touch INDEX.md (D39 — refresh_index owns it)
  - Strict citation format (validator check via D43)
"""

from __future__ import annotations

from handlers import HandlerContext, HandlerResult
from handlers._common import read_vault_schema, run_llm
from handlers.prompts.compile_to_wiki import SYSTEM_PROMPT
from praxis_core.logging import get_logger
from praxis_core.schemas.payloads import CompileToWikiPayload
from praxis_core.schemas.task_types import TaskModel
from praxis_core.vault import conventions as vc
from praxis_core.vault.backup import stash_for_edit

log = get_logger("handlers.compile_to_wiki")


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = CompileToWikiPayload.model_validate(ctx.payload)
    analysis_path = ctx.vault_root / payload.analysis_path
    ticker = (payload.ticker or "UNKNOWN").upper() if payload.ticker else "UNKNOWN"

    # D38 — pre-write backup of notes.md so validator can shrink-guard later
    if payload.ticker:
        notes_path = vc.company_notes_path(ctx.vault_root, payload.ticker)
        try:
            backup = stash_for_edit(notes_path, ctx.vault_root, category="compile")
            if backup:
                log.info("compile.backup_stashed", backup=str(backup))
        except Exception as e:
            log.warning("compile.backup_fail", error=str(e))

    user_prompt = f"""COMPILE TO WIKI

Source: {payload.source_kind}
Analysis at: {analysis_path}
Ticker: {ticker}
Accession: {payload.accession or "N/A"}

Your job:
1. Read the analysis at {analysis_path}.
2. Update or create <vault>/companies/{ticker}/notes.md — add a dated section
   summarizing the new information, with wikilinks back to the analysis file.
   The wikilink MUST appear as `[[{payload.analysis_path}]]` (brackets
   required).
3. Append to <vault>/companies/{ticker}/journal.md:
   `- <ISO date>: compiled {payload.source_kind} [[{payload.analysis_path}]]`.
4. Append to <vault>/LOG.md:
   `- <ISO timestamp> | compile | {ticker} | [[{payload.analysis_path}]]`.
5. Do NOT write to INDEX.md (refresh_index handles it).
6. If the analysis references themes or concepts, append a dated bullet to
   their `## Evidence` section linking back to the analysis. Add this
   company to their `## Related` if not already present. Do NOT rewrite
   existing theme/concept content.

Be concise. Exit when artifacts are written.
"""

    schema = read_vault_schema(ctx.vault_root)
    system = SYSTEM_PROMPT + ("\n\n## Vault schema\n" + schema if schema else "")

    result = await run_llm(
        system_prompt=system,
        user_prompt=user_prompt,
        model=TaskModel.SONNET,
        max_budget_usd=2.00,
        vault_root=ctx.vault_root,
    )
    log.info("compile_to_wiki.done", task_id=ctx.task_id, finish_reason=result.finish_reason)
    if result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=result, message="rate_limit")
    return HandlerResult(ok=True, llm_result=result)
