from __future__ import annotations

from handlers import HandlerContext, HandlerResult
from handlers._common import SYSTEM_PROMPT_PREFIX, read_vault_schema, run_llm
from praxis_core.logging import get_logger
from praxis_core.schemas.payloads import AnalyzeFilingPayload
from praxis_core.schemas.task_types import TaskModel
from praxis_core.vault import conventions as vc

log = get_logger("handlers.analyze_filing")


SYSTEM_PROMPT = (
    SYSTEM_PROMPT_PREFIX
    + """
Task: analyze_filing

Write a proper deep-read analysis of the filing. You will produce two artifacts:

(1) analysis.md — 500-1500 word analysis covering:
    - What happened (1 paragraph, cite specific lines from the filing)
    - Why it matters (market significance, not generic)
    - Downstream implications (who benefits, who hurts)
    - Questions this raises
    Cite the raw filing via `[[<raw_path>]]` for every specific claim.

(2) signals.json — structured, STRICT JSON matching this schema:
    {
      "accession": str,
      "ticker": str | null,
      "event_type": str,         // e.g. "earnings_guidance_update"
      "trade_relevant": bool,
      "urgency": "low"|"medium"|"high"|"intraday",
      "specific_claims": [str],  // 3-7 specific, sourced claims
      "linked_themes": [str],    // wiki theme handles you'd tag
      "linked_concepts": [str],  // wiki concept handles
      "thesis_impacts": [{"handle": str, "direction": "supportive"|"refutes"|"neutral", "confidence": 0-1}],
      "confidence": float 0-1,
      "summary": str             // one-sentence takeaway
    }

Both artifacts go in <vault>/_analyzed/filings/<form_type_lowercase>/<accession>/.
"""
)


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = AnalyzeFilingPayload.model_validate(ctx.payload)
    out_dir = vc.analyzed_filing_dir(ctx.vault_root, payload.form_type, payload.accession)
    raw_path = ctx.vault_root / payload.raw_path
    triage_path = ctx.vault_root / payload.triage_result_path

    user_prompt = f"""ANALYZE FILING

Accession: {payload.accession}
Form: {payload.form_type}
Ticker: {payload.ticker or "UNKNOWN"}

Inputs:
  - Raw filing: {raw_path}
  - Triage result: {triage_path}

Read BOTH files first, then produce analysis.md and signals.json in:
{out_dir}

Cite every quantitative claim with a wikilink like `[[{payload.raw_path}]]`.
If you reference existing wiki nodes (themes, concepts, companies), use their relative paths.
"""

    schema = read_vault_schema(ctx.vault_root)
    system = SYSTEM_PROMPT + ("\n\n## Vault schema\n" + schema if schema else "")

    result = await run_llm(
        system_prompt=system,
        user_prompt=user_prompt,
        model=TaskModel.SONNET,
        max_turns=12,
        vault_root=ctx.vault_root,
    )
    log.info(
        "analyze_filing.done",
        task_id=ctx.task_id,
        finish_reason=result.finish_reason,
        accession=payload.accession,
    )
    if result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=result, message="rate_limit")
    return HandlerResult(ok=True, llm_result=result)
