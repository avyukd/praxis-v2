from __future__ import annotations

import json
from pathlib import Path

from praxis_core.logging import get_logger
from praxis_core.schemas.payloads import TriageFilingPayload
from praxis_core.schemas.task_types import TaskModel
from praxis_core.vault import conventions as vc

from handlers import HandlerContext, HandlerResult
from handlers._common import SYSTEM_PROMPT_PREFIX, read_vault_schema, run_llm

log = get_logger("handlers.triage_filing")


SYSTEM_PROMPT = (
    SYSTEM_PROMPT_PREFIX
    + """
Task: triage_filing

You are deciding whether this SEC filing is worth a full deep-read. Be fast and decisive.

Score on a 1-5 scale:
  1 = noise (auto-exhibit, routine §8 with no material event)
  2 = boilerplate with minor detail
  3 = worth a quick read (new contract, guidance reaffirmation, director change)
  4 = clearly material (earnings beat/miss, guidance revision, major agreement, executive departure)
  5 = urgent (going concern, restatement, MNPI-grade disclosure, M&A announcement)

Categories: earnings | guidance | material_agreement | departure | acquisition | regulatory | other | noise

Emit `warrants_deep_read: true` if score >= 3.

Write two artifacts:
  (1) triage.md — human-readable one-paragraph rationale + citation wikilink to the raw filing.
  (2) triage.json — strict JSON conforming to this schema:
      {
        "accession": str,
        "form_type": str,
        "ticker": str | null,
        "score": int 1-5,
        "category": enum,
        "one_sentence_why": str,
        "warrants_deep_read": bool
      }

Artifacts go under: <vault>/_analyzed/filings/<form_type_lowercase>/<accession>/
"""
)


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = TriageFilingPayload.model_validate(ctx.payload)
    out_dir = vc.analyzed_filing_dir(ctx.vault_root, payload.form_type, payload.accession)
    raw_path = ctx.vault_root / payload.raw_path

    user_prompt = f"""TRIAGE FILING

Accession: {payload.accession}
Form: {payload.form_type}
Ticker: {payload.ticker or "UNKNOWN"}
CIK: {payload.cik}
Raw filing at: {raw_path}

Your job:
1. Read {raw_path}
2. Classify per the scoring rubric above.
3. Write {out_dir}/triage.md with a short rationale + `[[{payload.raw_path}]]` citation.
4. Write {out_dir}/triage.json with the strict JSON schema.

Create {out_dir} first via Bash if it doesn't exist, then use Write. Exit when done.
"""

    schema = read_vault_schema(ctx.vault_root)
    system = SYSTEM_PROMPT + ("\n\n## Vault schema\n" + schema if schema else "")

    result = await run_llm(
        system_prompt=system,
        user_prompt=user_prompt,
        model=TaskModel.HAIKU,
        max_turns=6,
        vault_root=ctx.vault_root,
    )
    log.info(
        "triage_filing.done",
        task_id=ctx.task_id,
        finish_reason=result.finish_reason,
        accession=payload.accession,
    )
    if result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=result, message="rate_limit")
    return HandlerResult(ok=True, llm_result=result)
