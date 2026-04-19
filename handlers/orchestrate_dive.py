from __future__ import annotations

import uuid

from sqlalchemy import select

from handlers import HandlerContext, HandlerResult
from handlers._common import SYSTEM_PROMPT_PREFIX, read_vault_schema, run_llm
from praxis_core.db.models import Investigation
from praxis_core.db.session import session_scope
from praxis_core.logging import get_logger
from praxis_core.schemas.payloads import OrchestrateDivePayload
from praxis_core.schemas.task_types import TaskModel, TaskType
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.time_et import et_date_str, et_iso
from praxis_core.vault import conventions as vc
from praxis_core.vault.writer import write_markdown_with_frontmatter

log = get_logger("handlers.orchestrate_dive")


SYSTEM_PROMPT = (
    SYSTEM_PROMPT_PREFIX
    + """
Task: orchestrate_dive

You are planning a multi-task deep dive on a company. Given the current state of the wiki
for this ticker, emit a plan of which specialist dive tasks to run.

For Monday's MVP, valid dive specialists are:
  - dive_business     (understand segments, unit economics, revenue mix)
  - dive_moat         (moat sources, durability, evidence)
  - dive_financials   (5yr trajectory, margins, balance sheet)

Output:
(1) Write <vault>/investigations/<handle>.md with:
    - YAML frontmatter: type, status, scope, hypothesis, entry_nodes, created_at
    - ## Plan section listing the dive tasks you plan to run (in order)
    - ## Log section (empty initially)
(2) Do NOT enqueue the specialist tasks yourself — the dispatcher reads the plan and does it.
    Your artifact IS the investigation file with the plan.
"""
)


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = OrchestrateDivePayload.model_validate(ctx.payload)

    inv_path = vc.investigation_path(ctx.vault_root, payload.investigation_handle)
    now_iso = et_iso()

    company_notes = vc.company_notes_path(ctx.vault_root, payload.ticker)
    notes_excerpt = ""
    if company_notes.exists():
        notes_excerpt = company_notes.read_text(encoding="utf-8")[:3000]

    user_prompt = f"""ORCHESTRATE DIVE

Ticker: {payload.ticker}
Investigation handle: {payload.investigation_handle}
Thesis handle: {payload.thesis_handle or "(none)"}

Current state of <vault>/companies/{payload.ticker}/notes.md (first 3000 chars):
---
{notes_excerpt or "(empty — this is a fresh company)"}
---

Emit the plan at {inv_path}.

Frontmatter required:
  type: investigation
  status: active
  scope: company
  initiated_by: {"observer" if "observer" in (payload.thesis_handle or "") else "user"}
  hypothesis: <1-2 sentence hypothesis>
  entry_nodes: [companies/{payload.ticker}]
  created_at: {now_iso}

Plan section — list dive tasks in execution order, rationalize briefly. E.g.:
  1. dive_business — understand segments before anything else
  2. dive_moat — evaluate durability
  3. dive_financials — quantify the story
  4. synthesize_memo — crystallize as dated memo

If notes already cover a section well, you can skip that dive. Be pragmatic.
"""

    schema = read_vault_schema(ctx.vault_root)
    system = SYSTEM_PROMPT + ("\n\n## Vault schema\n" + schema if schema else "")

    result = await run_llm(
        system_prompt=system,
        user_prompt=user_prompt,
        model=TaskModel.SONNET,
        max_turns=6,
        vault_root=ctx.vault_root,
    )
    log.info("orchestrate_dive.done", task_id=ctx.task_id, finish_reason=result.finish_reason)
    if result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=result, message="rate_limit")

    async def _ensure_investigation(s) -> None:
        inv = (
            await s.execute(
                select(Investigation).where(Investigation.handle == payload.investigation_handle)
            )
        ).scalar_one_or_none()
        if inv is None:
            inv = Investigation(
                handle=payload.investigation_handle,
                status="active",
                scope="company",
                initiated_by="orchestrator",
                hypothesis=f"Deep dive into {payload.ticker}",
                entry_nodes=[f"companies/{payload.ticker}"],
                vault_path=str(inv_path.relative_to(ctx.vault_root)),
            )
            s.add(inv)

    # Ensure investigation record exists, using worker session if passed
    if ctx.session is not None:
        await _ensure_investigation(ctx.session)
    else:
        async with session_scope() as session:
            await _ensure_investigation(session)

    # Ensure the investigation file exists even if the LLM didn't write it
    if not inv_path.exists():
        write_markdown_with_frontmatter(
            inv_path,
            body=f"# Investigation: {payload.ticker}\n\n## Plan\n1. dive_business\n2. dive_moat\n3. dive_financials\n4. synthesize_memo\n\n## Log\n",
            metadata={
                "type": "investigation",
                "status": "active",
                "scope": "company",
                "initiated_by": "orchestrator",
                "entry_nodes": [f"companies/{payload.ticker}"],
                "tags": ["investigation"],
            },
        )

    # Enqueue the specialist dive tasks for this investigation
    plan_sequence: list[tuple[TaskType, dict]] = [
        (
            TaskType.DIVE_BUSINESS,
            {
                "ticker": payload.ticker,
                "investigation_handle": payload.investigation_handle,
            },
        ),
        (
            TaskType.DIVE_MOAT,
            {
                "ticker": payload.ticker,
                "investigation_handle": payload.investigation_handle,
            },
        ),
        (
            TaskType.DIVE_FINANCIALS,
            {
                "ticker": payload.ticker,
                "investigation_handle": payload.investigation_handle,
            },
        ),
        (
            TaskType.SYNTHESIZE_MEMO,
            {
                "ticker": payload.ticker,
                "investigation_handle": payload.investigation_handle,
                "thesis_handle": payload.thesis_handle,
                "memo_handle": f"{payload.ticker.lower()}-dive-{et_date_str().replace('-', '')}",
            },
        ),
    ]

    async def _enqueue_sequence(s) -> None:
        inv = (
            await s.execute(
                select(Investigation).where(Investigation.handle == payload.investigation_handle)
            )
        ).scalar_one()
        prior: uuid.UUID | None = None
        for task_type, sub_payload in plan_sequence:
            dep = [prior] if prior else None
            new_id = await enqueue_task(
                s,
                task_type=task_type,
                payload=sub_payload,
                priority=2,  # P2: Loop B dive lane
                dedup_key=f"{task_type.value}:{payload.investigation_handle}",
                investigation_id=inv.id,
                depends_on=dep,
            )
            if new_id:
                prior = new_id

    if ctx.session is not None:
        await _enqueue_sequence(ctx.session)
    else:
        async with session_scope() as session:
            await _enqueue_sequence(session)

    return HandlerResult(ok=True, llm_result=result)
