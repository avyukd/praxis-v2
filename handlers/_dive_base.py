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

import json
import re

from handlers import HandlerContext, HandlerResult
from handlers._common import DIVE_ALLOWED_TOOLS, read_vault_schema, run_llm
from handlers.prompts.dive_reflect import REFLECT_SYSTEM_PROMPT
from praxis_core.logging import get_logger
from praxis_core.research.budget import ResearchBudget
from praxis_core.schemas.task_types import TaskModel
from praxis_core.tasks.investigations import touch_investigation
from praxis_core.time_et import et_iso
from praxis_core.vault import conventions as vc
from praxis_core.vault.constitution import constitution_prompt_block
from praxis_core.vault.followups import write_followup
from praxis_core.vault.writer import atomic_write

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


def _write_skeleton(
    output_path: Path, ticker: str, specialty_slug: str, specialty_label: str
) -> None:
    """Create a skeleton file before the LLM starts so that if we SIGINT
    mid-generation (or hit timeout), there's always something on disk.
    The LLM is instructed to Edit this file progressively as it works —
    so partial work accumulates incrementally, and a kill anywhere loses
    at most the last section."""
    if output_path.exists():
        return
    skeleton = f"""---
type: dive
specialist: {specialty_slug}
ticker: {ticker}
data_vintage: {et_iso()[:10]}
status: in-progress
---

# {ticker} — {specialty_label}

## Verdict
_[dive in progress — will be filled]_

## Sources consulted
_[will be populated as retrieval completes]_
"""
    atomic_write(output_path, skeleton)


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

    # Pre-write a skeleton so partial work is preserved across kill
    # scenarios. The LLM is told below to Edit this in place, section by
    # section, rather than writing everything at once at the end.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_skeleton(output_path, ticker, specialty_slug, specialty_label)

    user_prompt = f"""DIVE: {specialty_label}

Ticker: {ticker}
Investigation: {investigation_handle or "(standalone)"}
Specialty: {specialty_slug}
Research depth: **{budget.depth_label}**

Research budget:
  - Word cap for this dive: ~{budget.specialist_words} words (controls
    output size; keep the dive tight and table-heavy)
  - Web lookups: unlimited — fetch every primary source you need. SEC
    filings, SEDAR+ filings, issuer IR pages, FRED, competitor 10-Ks,
    earnings transcripts, whatever it takes. Do not stop early.
  - Tool-call policy: {budget.agent_policy} (depth of fundamentals-MCP
    exploration — run as many `get_full_statement`, `get_earnings`,
    `search_fundamentals` calls as the analysis needs)

Output file (a skeleton already exists at this path — use the Edit tool
to update it progressively as you complete each section; this preserves
partial work if the subprocess is interrupted):
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

2. **Edit the skeleton file progressively.** The file at {output_path}
   already exists with section headers. Use the **Edit tool** to replace
   each section's placeholder as you complete it, not a single Write at
   the end. Order: retrieve some data → Edit in the "## Sources consulted"
   row for those tool calls + Edit in a partial section → continue. If
   the subprocess is interrupted, whatever is in the file at that moment
   is what will be used, so keep it current.

3. **Cover the full structure** defined in the system prompt. Honor the
   word cap — better to compress than to sprawl.

4. **Document your sources** in the `## Sources consulted` section at the
   bottom. Every tool call with its outcome (e.g. "get_full_statement(...)
   → FY2024 revenue $X"). This is how the validator verifies research
   depth. Update this section as you go, not at the end.

5. **Do not overwrite existing sibling dives** in companies/<TICKER>/dives/
   (other specialists own those).

6. **Log in the investigation file** (if it exists): append one line to
   its `## Log` section:
   `- {et_iso()}: {specialty_slug} completed ({budget.depth_label})`
"""

    schema = read_vault_schema(ctx.vault_root)
    constitution = constitution_prompt_block(ctx.vault_root)
    system = system_prompt + (
        ("\n\n" + constitution) if constitution else ""
    ) + ("\n\n## Vault schema\n" + schema if schema else "")

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

    try:
        await _reflect_and_write_followups(
            ctx=ctx,
            output_path=output_path,
            ticker=ticker,
            specialty_slug=specialty_slug,
            investigation_handle=investigation_handle,
        )
    except Exception as e:
        log.warning(
            "dive.reflect_fail",
            specialty=specialty_slug,
            ticker=ticker,
            error=str(e)[:200],
        )

    return HandlerResult(ok=True, llm_result=result)


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


async def _reflect_and_write_followups(
    *,
    ctx: HandlerContext,
    output_path: Path,
    ticker: str,
    specialty_slug: str,
    investigation_handle: str,
) -> None:
    """Short Haiku call after the dive: 'what would you want to investigate
    next?' Followup questions go to vault/questions/. Dedup by (title,
    ticker) hash so repeated dives don't spam duplicates.
    """
    if not output_path.exists():
        return
    try:
        dive_text = output_path.read_text(encoding="utf-8")
    except OSError:
        return
    excerpt = dive_text[:6000]

    user_prompt = (
        f"Ticker: {ticker}\n"
        f"Specialty: {specialty_slug}\n"
        f"Investigation: {investigation_handle or '(standalone)'}\n\n"
        "Dive content (first 6000 chars):\n---\n"
        f"{excerpt}\n---\n\n"
        "Emit up to 3 followup questions per schema. Return JSON only."
    )

    result = await run_llm(
        system_prompt=REFLECT_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=TaskModel.HAIKU,
        max_budget_usd=0.15,
        vault_root=ctx.vault_root,
        allowed_tools=[],
    )
    if result.finish_reason == "rate_limit":
        log.info("dive.reflect_rate_limit", specialty=specialty_slug, ticker=ticker)
        return

    text = (result.text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    m = _JSON_OBJ_RE.search(text)
    if not m:
        log.info("dive.reflect_no_json", specialty=specialty_slug, ticker=ticker)
        return
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        log.info("dive.reflect_bad_json", specialty=specialty_slug, ticker=ticker)
        return

    questions = data.get("questions") or []
    if not isinstance(questions, list):
        return

    written = 0
    for q in questions[:3]:
        if not isinstance(q, dict):
            continue
        title = str(q.get("title") or "").strip()
        body = str(q.get("body") or "").strip()
        priority = str(q.get("priority") or "medium").lower()
        if priority not in ("low", "medium", "high"):
            priority = "medium"
        if not title or not body:
            continue
        result_path = write_followup(
            ctx.vault_root,
            title=title,
            body=body,
            origin_task_type=ctx.task_type,
            ticker=ticker,
            investigation_handle=investigation_handle or None,
            priority=priority,
            tags=[f"origin:{specialty_slug}"],
        )
        if result_path is not None:
            written += 1
    log.info(
        "dive.reflect_done",
        specialty=specialty_slug,
        ticker=ticker,
        proposed=len(questions),
        written=written,
    )
