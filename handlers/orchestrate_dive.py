from __future__ import annotations

from sqlalchemy import select

from handlers import HandlerContext, HandlerResult
from handlers._common import SYSTEM_PROMPT_PREFIX, read_vault_schema, run_llm
from handlers._plan_parser import parse_plan
from praxis_core.db.models import Investigation
from praxis_core.db.session import session_scope
from praxis_core.logging import get_logger
from praxis_core.schemas.payloads import OrchestrateDivePayload
from praxis_core.schemas.task_types import TaskModel, TaskType
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.tasks.investigations import touch_investigation
from praxis_core.time_et import et_date_str, et_iso
from praxis_core.vault import conventions as vc
from praxis_core.vault.coverage import find_existing_coverage
from praxis_core.vault.writer import write_markdown_with_frontmatter

log = get_logger("handlers.orchestrate_dive")


SYSTEM_PROMPT = (
    SYSTEM_PROMPT_PREFIX
    + """
Task: orchestrate_dive

You are planning a multi-task deep dive on a company. Given the current state
of the wiki for this ticker, emit a plan of which specialist dive tasks to
run, then synthesize_memo at the end.

**Valid specialist task types** (use these EXACT names in the plan — any
other names will silently be dropped by the plan parser):

  - dive_financial_rigorous   — earnings quality, cash flow, balance sheet,
                                normalized earnings, INVESTABILITY verdict
                                (gating: if STOP, siblings are canceled)
  - dive_business_moat        — business model, segments, unit economics,
                                competitive durability, switching costs,
                                pricing power, network effects
  - dive_industry_structure   — industry economics, Porter's forces, cycle
                                position, structural trends, company's
                                position within
  - dive_capital_allocation   — M&A track record, buybacks, dividends,
                                dilution, SBC, ROIIC, insider alignment
  - dive_geopolitical_risk    — cross-border exposure, sanctions,
                                tariff/trade policy, regulated-industry
                                risk. Skip if domestic-only + unregulated.
  - dive_macro                — rate/commodity/FX/cycle sensitivity. Skip
                                if genuinely macro-neutral.
  - dive_custom               — anything outside the above (e.g. "legal
                                overhang", "pipeline Phase-3 readout") —
                                orchestrator specifies specialty + focus
  - synthesize_memo           — coordinator-level memo with Buy/Sell/
                                Neutral/Too Hard decision + target price.
                                ALWAYS last in the plan.

Output:

(1) Write <vault>/investigations/<handle>.md with:
    - YAML frontmatter: type, status, scope, hypothesis, entry_nodes,
      created_at
    - ## Plan section listing tasks in execution order (numbered), with
      a short rationale each. The parser extracts task names, so write
      the exact task-type name at the start of each list item.
    - ## Log section (empty initially)

(2) Your plan MUST include dive_financial_rigorous as the first specialist
    (it gates sibling dispatch via its INVESTABILITY verdict). It MUST
    include synthesize_memo as the last task.

(3) For a fresh ticker with no prior research (empty notes.md), emit the
    full 4-dive default: financial_rigorous, business_moat, industry_structure,
    capital_allocation, then synthesize_memo. Add geopolitical_risk / macro
    only if the company has real exposure there (cross-border, commodity,
    rate-sensitive). Add dive_custom when there's a specific overhang to
    investigate.

(4) If notes already cover a specialty well AND it's less than 30 days old,
    you MAY skip that specialty. Be pragmatic — err on including a dive
    when in doubt.

(5) Do NOT enqueue the specialist tasks yourself — the dispatcher reads
    the plan and does it.
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

Plan section — list dive tasks in execution order, rationalize briefly.
Use the EXACT task names from the system prompt. For a fresh ticker
(empty notes.md), emit this default 5-item plan:

  1. dive_financial_rigorous — earnings quality + INVESTABILITY gate
  2. dive_business_moat — segments, unit economics, durability
  3. dive_industry_structure — cycle position + competitive landscape
  4. dive_capital_allocation — stewardship track record
  5. synthesize_memo — Buy/Sell/Neutral/Too Hard + target price

Add dive_geopolitical_risk and/or dive_macro when the company has real
exposure there. If notes already cover a specialty within 30 days, you
may skip that specialty. Err on running the dive when in doubt.
"""

    schema = read_vault_schema(ctx.vault_root)
    system = SYSTEM_PROMPT + ("\n\n## Vault schema\n" + schema if schema else "")

    result = await run_llm(
        system_prompt=system,
        user_prompt=user_prompt,
        model=TaskModel.SONNET,
        max_budget_usd=0.50,  # short planner call
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

    # Default plan if LLM didn't write one or we can't parse it (D19 taxonomy)
    default_plan: list[TaskType] = [
        TaskType.DIVE_FINANCIAL_RIGOROUS,
        TaskType.DIVE_BUSINESS_MOAT,
        TaskType.DIVE_INDUSTRY_STRUCTURE,
        TaskType.DIVE_CAPITAL_ALLOCATION,
        TaskType.SYNTHESIZE_MEMO,
    ]

    # Ensure the investigation file exists even if the LLM didn't write it
    if not inv_path.exists():
        default_body = (
            f"# Investigation: {payload.ticker}\n\n"
            "## Plan\n"
            + "\n".join(f"{i + 1}. {t.value}" for i, t in enumerate(default_plan))
            + "\n\n## Log\n"
        )
        write_markdown_with_frontmatter(
            inv_path,
            body=default_body,
            metadata={
                "type": "investigation",
                "status": "active",
                "scope": "company",
                "initiated_by": "orchestrator",
                "entry_nodes": [f"companies/{payload.ticker}"],
                "tags": ["investigation"],
            },
        )

    # Parse the LLM's plan; fall back to default if it's empty/unparseable.
    try:
        plan_text = inv_path.read_text(encoding="utf-8")
    except OSError:
        plan_text = ""
    parsed_plan = parse_plan(plan_text)
    # Defensive: the LLM sometimes uses renamed or old task-type names
    # (e.g. dive_business instead of dive_business_moat) — the parser
    # silently drops unknown names, leaving behind just synthesize_memo.
    # If the parsed plan has no dive_* specialists, treat as invalid
    # and fall back to the default plan. Otherwise we'd write a
    # meaningless "Too Hard" memo on zero research.
    has_specialist = any(t.value.startswith("dive_") for t in parsed_plan)
    if parsed_plan and not has_specialist:
        log.warning(
            "orchestrate_dive.plan_has_no_specialists",
            task_id=ctx.task_id,
            parsed_types=[t.value for t in parsed_plan],
            action="falling back to default_plan",
        )
    if parsed_plan and has_specialist:
        plan_types = parsed_plan
    else:
        plan_types = default_plan

    # D24 coverage skip: if fresh themes/concepts already cover the
    # geopolitical or macro dimensions, drop those specialists from the
    # plan to avoid re-deriving content that's already in the vault.
    # Never skip financial_rigorous (gating) or synthesize_memo (terminal).
    coverage = find_existing_coverage(
        ctx.vault_root, payload.ticker, ["geopolitical", "macro"]
    )
    skipped: list[str] = []
    if coverage["geopolitical"] and TaskType.DIVE_GEOPOLITICAL_RISK in plan_types:
        plan_types = [t for t in plan_types if t != TaskType.DIVE_GEOPOLITICAL_RISK]
        skipped.append(
            f"dive_geopolitical_risk (covered by {len(coverage['geopolitical'])} vault files)"
        )
    if coverage["macro"] and TaskType.DIVE_MACRO in plan_types:
        plan_types = [t for t in plan_types if t != TaskType.DIVE_MACRO]
        skipped.append(f"dive_macro (covered by {len(coverage['macro'])} vault files)")

    log.info(
        "orchestrate_dive.plan_resolved",
        task_id=ctx.task_id,
        parsed_count=len(parsed_plan),
        using=[t.value for t in plan_types],
        coverage_skipped=skipped,
    )

    memo_handle = f"{payload.ticker.lower()}-dive-{et_date_str().replace('-', '')}"

    async def _enqueue_sequence(s) -> None:
        inv = (
            await s.execute(
                select(Investigation).where(Investigation.handle == payload.investigation_handle)
            )
        ).scalar_one()

        # Propagate the investigation's research_priority down to every dive
        # + synthesize_memo task. The dive base uses this to size the
        # ResearchBudget (word cap, web lookups, LLM $ budget).
        research_priority = getattr(inv, "research_priority", 5) or 5

        def _payload_for(task_type: TaskType) -> dict:
            base = {
                "ticker": payload.ticker,
                "investigation_handle": payload.investigation_handle,
                "research_priority": research_priority,
            }
            if task_type == TaskType.SYNTHESIZE_MEMO:
                base["thesis_handle"] = payload.thesis_handle
                base["memo_handle"] = memo_handle
            return base

        plan_sequence = [(t, _payload_for(t)) for t in plan_types]
        for task_type, sub_payload in plan_sequence:
            await enqueue_task(
                s,
                task_type=task_type,
                payload=sub_payload,
                priority=2,  # P2: Loop B dive lane
                dedup_key=f"{task_type.value}:{payload.investigation_handle}",
                investigation_id=inv.id,
            )
        await touch_investigation(s, inv.id)

    if ctx.session is not None:
        await _enqueue_sequence(ctx.session)
    else:
        async with session_scope() as session:
            await _enqueue_sequence(session)

    return HandlerResult(ok=True, llm_result=result)
