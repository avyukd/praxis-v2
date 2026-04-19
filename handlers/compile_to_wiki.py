from __future__ import annotations

from handlers import HandlerContext, HandlerResult
from handlers._common import SYSTEM_PROMPT_PREFIX, read_vault_schema, run_llm
from praxis_core.logging import get_logger
from praxis_core.schemas.payloads import CompileToWikiPayload
from praxis_core.schemas.task_types import TaskModel

log = get_logger("handlers.compile_to_wiki")


SYSTEM_PROMPT = (
    SYSTEM_PROMPT_PREFIX
    + """
Task: compile_to_wiki

You are compiling per-filing analysis into the living wiki. Per Karpathy's LLM wiki pattern:
- Touch 5+ pages per compile: company notes, company journal, INDEX, LOG, and any affected
  theme/concept/person notes.
- Every wikilink must have a bidirectional backlink (add to the target's ## Related section
  AND its `links:` frontmatter list).
- Append a one-line entry to LOG.md at vault root with timestamp + summary + ticker(s).
- Update INDEX.md if a new node was created or an existing node gained material content.

Artifacts validator checks:
  - INDEX.md updated
  - LOG.md updated
  - companies/<TICKER>/notes.md updated (if ticker known)
  - companies/<TICKER>/journal.md updated (if ticker known)
  - at least 3 files touched total

Do NOT rewrite existing content. Append, link, refine. Maintain frontmatter.
"""
)


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = CompileToWikiPayload.model_validate(ctx.payload)
    analysis_path = ctx.vault_root / payload.analysis_path

    user_prompt = f"""COMPILE TO WIKI

Source: {payload.source_kind}
Analysis at: {analysis_path}
Ticker: {payload.ticker or "UNKNOWN"}
Accession: {payload.accession or "N/A"}

Your job:
1. Read the analysis at {analysis_path}.
2. Update or create <vault>/companies/{payload.ticker or "<TICKER>"}/notes.md — add a dated section
   summarizing the new information, with wikilinks back to the analysis file.
3. Append to <vault>/companies/{payload.ticker or "<TICKER>"}/journal.md:
   `- <ISO date>: compiled {payload.source_kind} [[{payload.analysis_path}]]`.
4. Update <vault>/INDEX.md — if this ticker is new, add to the Companies section;
   otherwise update the "last touched" timestamp for the ticker.
5. Append to <vault>/LOG.md:
   `- <ISO timestamp> | compile | {payload.ticker} | [[{payload.analysis_path}]]`.
6. If the analysis references themes or concepts, update those notes too — add a dated bullet
   to their `## Evidence` section linking back to the analysis, and add this company to
   their `## Related` if not already present.

Be concise. Exit when artifacts are written.
"""

    schema = read_vault_schema(ctx.vault_root)
    system = SYSTEM_PROMPT + ("\n\n## Vault schema\n" + schema if schema else "")

    result = await run_llm(
        system_prompt=system,
        user_prompt=user_prompt,
        model=TaskModel.SONNET,
        max_budget_usd=2.00,  # touches many files, multi-turn
        vault_root=ctx.vault_root,
    )
    log.info("compile_to_wiki.done", task_id=ctx.task_id, finish_reason=result.finish_reason)
    if result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=result, message="rate_limit")
    return HandlerResult(ok=True, llm_result=result)
