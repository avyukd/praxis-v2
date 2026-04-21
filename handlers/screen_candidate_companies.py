"""screen_candidate_companies — rank candidate tickers, enqueue deep dives
for the top-N, leave the rest as note_only or reject.
"""

from __future__ import annotations

import json
import re

from sqlalchemy import select

from handlers import HandlerContext, HandlerResult
from handlers._common import run_llm
from handlers.prompts.research_handlers import SCREEN_CANDIDATE_COMPANIES_PROMPT
from praxis_core.db.models import Investigation
from praxis_core.db.session import session_scope
from praxis_core.logging import get_logger
from praxis_core.schemas.payloads import ScreenCandidateCompaniesPayload
from praxis_core.schemas.task_types import TaskModel, TaskType
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.time_et import et_date_str
from praxis_core.vault import conventions as vc
from praxis_core.vault.constitution import constitution_prompt_block
from praxis_core.vault.section_append import append_to_section

log = get_logger("handlers.screen_candidate_companies")


SCREEN_ALLOWED_TOOLS = [
    "Read",
    "Glob",
    "Grep",
    "mcp__praxis__search_vault",
    "mcp__fundamentals__company_overview",
    "mcp__fundamentals__get_price",
    "mcp__fundamentals__search_fundamentals",
]


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_verdicts(raw: str) -> list[dict] | None:
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    m = _JSON_OBJ_RE.search(text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    ranked = data.get("ranked")
    if not isinstance(ranked, list):
        return None
    return ranked


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = ScreenCandidateCompaniesPayload.model_validate(ctx.payload)

    constitution = constitution_prompt_block(ctx.vault_root)
    system = SCREEN_CANDIDATE_COMPANIES_PROMPT + (
        ("\n\n" + constitution) if constitution else ""
    )

    tickers_block = "\n".join(f"- {t}" for t in payload.tickers)
    user_prompt = (
        "SCREEN CANDIDATE COMPANIES\n\n"
        f"**Investigation:** {payload.investigation_handle}\n"
        f"**Subject:** {payload.subject}\n"
        f"**Ranking question:** {payload.ranking_question}\n"
        f"**Max deep dives:** {payload.max_deep_dives}\n\n"
        "## Candidates\n"
        f"{tickers_block}\n\n"
        "Emit JSON per the schema. `deep_dive` verdicts enqueue real "
        "company investigations downstream."
    )

    result = await run_llm(
        system_prompt=system,
        user_prompt=user_prompt,
        model=TaskModel.SONNET,
        max_budget_usd=1.00,
        vault_root=ctx.vault_root,
        allowed_tools=SCREEN_ALLOWED_TOOLS,
    )
    log.info(
        "screen_candidate_companies.llm_done",
        task_id=ctx.task_id,
        handle=payload.investigation_handle,
        finish_reason=result.finish_reason,
    )
    if result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=result, message="rate_limit")

    verdicts = _parse_verdicts(result.text) or []
    deep_dives = [v for v in verdicts if isinstance(v, dict) and v.get("verdict") == "deep_dive"]
    deep_dives = deep_dives[: payload.max_deep_dives]

    # Persist verdicts into the investigation file for audit
    inv_path = vc.investigation_path(ctx.vault_root, payload.investigation_handle)
    if inv_path.exists() and verdicts:
        lines = ["", "### Screening verdicts", ""]
        for v in verdicts:
            if not isinstance(v, dict):
                continue
            lines.append(
                f"- **{v.get('ticker')}** — {v.get('verdict','?')}"
                f" (exposure {v.get('exposure_score', '?')}, "
                f"inv {v.get('investability_score','?')}): "
                f"{v.get('why','')[:200]}"
            )
        try:
            append_to_section(
                inv_path,
                "## Log",
                "\n".join(lines),
                dedup_substring=f"Screening verdicts ({et_date_str()})",
            )
        except Exception as e:
            log.warning("screen.log_append_fail", error=str(e))

    # Enqueue orchestrate_dive for each deep_dive verdict under a new
    # company-scoped investigation (reuses the proven company engine).
    enqueued = 0
    async def _enqueue_dives(s) -> None:
        nonlocal enqueued
        parent = (
            await s.execute(
                select(Investigation).where(
                    Investigation.handle == payload.investigation_handle
                )
            )
        ).scalar_one_or_none()
        parent_id = parent.id if parent else None

        for v in deep_dives:
            ticker = str(v.get("ticker", "")).upper().strip()
            if not ticker:
                continue
            child_handle = (
                f"{ticker.lower()}-from-{payload.investigation_handle[:60]}"
            )
            # Create a child investigation row so the dive is attributed
            child = (
                await s.execute(
                    select(Investigation).where(Investigation.handle == child_handle)
                )
            ).scalar_one_or_none()
            if child is None:
                parent_ref = (
                    [f"investigations/{payload.investigation_handle}"]
                    if parent_id else []
                )
                child = Investigation(
                    handle=child_handle,
                    status="active",
                    scope="company",
                    initiated_by=f"research_screening:{payload.investigation_handle}",
                    hypothesis=(v.get("why") or payload.ranking_question)[:500],
                    entry_nodes=[f"companies/{ticker}"] + parent_ref,
                    vault_path=f"investigations/{child_handle}.md",
                    research_priority=7 if v.get("exposure_score", 0) >= 0.75 else 5,
                )
                s.add(child)
                await s.flush()
            await enqueue_task(
                s,
                task_type=TaskType.ORCHESTRATE_DIVE,
                payload={
                    "ticker": ticker,
                    "investigation_handle": child_handle,
                    "thesis_handle": None,
                    "research_priority": child.research_priority,
                },
                priority=2,
                dedup_key=f"screen_dive:{ticker}:{payload.investigation_handle}",
                investigation_id=child.id,
            )
            enqueued += 1

    if deep_dives:
        if ctx.session is not None:
            await _enqueue_dives(ctx.session)
        else:
            async with session_scope() as session:
                await _enqueue_dives(session)

    log.info(
        "screen_candidate_companies.done",
        task_id=ctx.task_id,
        handle=payload.investigation_handle,
        verdicts_total=len(verdicts),
        deep_dives_enqueued=enqueued,
    )
    return HandlerResult(ok=True, llm_result=result)
