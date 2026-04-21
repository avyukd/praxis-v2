"""orchestrate_research — broad-topic planner entrypoint.

Receives a freeform research prompt, consults vault memory for nearest
neighbors (theme/question/concept/memo/company/source dedup), asks
Sonnet to produce a structured plan, writes the investigation file,
and enqueues the downstream phase tasks (gather_sources + compile +
answer + screen + synthesize).

The plan format is JSON — see handlers/prompts/orchestrate_research.py
for the schema. Parser is tolerant: malformed or partial plans fall
back to a minimal default (gather_sources only) so a bad planner call
doesn't lose the investigation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select

from handlers import HandlerContext, HandlerResult
from handlers._common import SYSTEM_PROMPT_PREFIX, read_vault_schema, run_llm
from handlers.prompts.orchestrate_research import SYSTEM_PROMPT
from praxis_core.db.models import Investigation
from praxis_core.db.session import session_scope
from praxis_core.logging import get_logger
from praxis_core.schemas.payloads import OrchestrateResearchPayload
from praxis_core.schemas.task_types import TaskModel, TaskType
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.tasks.investigations import touch_investigation
from praxis_core.time_et import et_date_str, et_iso
from praxis_core.vault import conventions as vc
from praxis_core.vault.constitution import constitution_prompt_block
from praxis_core.vault.memory import search_vault_memory
from praxis_core.vault.steering import recent_steering
from praxis_core.vault.writer import write_markdown_with_frontmatter

log = get_logger("handlers.orchestrate_research")


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _render_memory_block(hits: list) -> str:
    if not hits:
        return "(vault is thin on this topic — no nearest neighbors)"
    lines = []
    for h in hits:
        lines.append(
            f"- [{h.node_type}] [[{h.path}]] — {h.title[:80]}\n"
            f"    relevance={h.relevance_score:.2f}  why: {h.why_relevant or h.snippet[:120]}"
        )
    return "\n".join(lines)


def _parse_plan(raw_text: str) -> dict | None:
    text = (raw_text or "").strip()
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
    if not isinstance(data, dict):
        return None
    return data


def _default_plan(prompt: str) -> dict:
    """Minimal fallback when the LLM response doesn't parse."""
    return {
        "scope_type": "crosscutting",
        "subject": prompt[:180],
        "hypothesis": "(planner output unparseable — running retrieval-only fallback)",
        "theme_nodes": [],
        "question_nodes": [],
        "concept_nodes": [],
        "retrieval_queries": [prompt[:200]],
        "candidate_tickers": [],
        "tickers_to_deep_dive": [],
        "final_artifact": {
            "kind": "crosscut_memo",
            "memo_handle": f"research-{et_date_str().replace('-', '')}",
        },
    }


def _investigation_body(
    payload: OrchestrateResearchPayload, plan: dict, memory_block: str
) -> str:
    q_nodes = plan.get("question_nodes", [])
    t_nodes = plan.get("theme_nodes", [])
    c_nodes = plan.get("concept_nodes", [])
    queries = plan.get("retrieval_queries", [])
    tickers = plan.get("candidate_tickers", [])
    subj = plan.get("subject", "")
    hyp = plan.get("hypothesis", "")

    lines: list[str] = []
    lines.append(f"# Research: {subj or payload.prompt[:120]}")
    lines.append("")
    lines.append(f"**Prompt:** {payload.prompt}")
    lines.append("")
    lines.append(f"**Scope:** {plan.get('scope_type', 'crosscutting')}")
    lines.append("")
    lines.append(f"**Hypothesis:** {hyp}")
    lines.append("")
    lines.append("## Plan")
    lines.append("")
    if t_nodes:
        lines.append("### Theme nodes")
        for n in t_nodes:
            lines.append(
                f"- [[themes/{n.get('slug')}]] — {n.get('action','update')} "
                f"— {n.get('why','')}"
            )
        lines.append("")
    if q_nodes:
        lines.append("### Question nodes")
        for n in q_nodes:
            lines.append(
                f"- [[questions/{n.get('slug')}]] — {n.get('action','create')} "
                f"— {n.get('why','')}"
            )
        lines.append("")
    if c_nodes:
        lines.append("### Concept nodes")
        for n in c_nodes:
            lines.append(
                f"- [[concepts/{n.get('slug')}]] — {n.get('action','update')} "
                f"— {n.get('why','')}"
            )
        lines.append("")
    if queries:
        lines.append("### Retrieval queries")
        for q in queries:
            lines.append(f"- `{q}`")
        lines.append("")
    if tickers:
        lines.append("### Candidate tickers")
        lines.append("")
        lines.append(", ".join(tickers))
        lines.append("")
    lines.append("## Vault memory (nearest neighbors at plan time)")
    lines.append("")
    lines.append(memory_block)
    lines.append("")
    lines.append("## Log")
    lines.append("")
    return "\n".join(lines) + "\n"


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = OrchestrateResearchPayload.model_validate(ctx.payload)

    # Phase 0 — consult vault memory for nearest neighbors
    hits = await search_vault_memory(
        ctx.vault_root,
        payload.prompt,
        limit=12,
    )
    memory_block = _render_memory_block(hits)

    # Phase 1 — plan with Sonnet
    constitution = constitution_prompt_block(ctx.vault_root)
    steering = recent_steering(ctx.vault_root, max_entries=8)
    schema = read_vault_schema(ctx.vault_root)
    system = SYSTEM_PROMPT + (
        ("\n\n" + constitution) if constitution else ""
    ) + ("\n\n## Vault schema\n" + schema if schema else "")

    user_prompt_parts = [
        "ORCHESTRATE RESEARCH",
        "",
        f"**Prompt:** {payload.prompt}",
        f"**Research priority:** {payload.research_priority}/10",
        f"**Investigation handle:** {payload.investigation_handle}",
    ]
    if steering:
        user_prompt_parts.extend(["", steering])
    user_prompt_parts.extend(
        [
            "",
            "## Vault memory — nearest-neighbor results",
            "",
            memory_block,
            "",
            "Emit the plan JSON per the schema.",
        ]
    )
    user_prompt = "\n".join(user_prompt_parts)

    result = await run_llm(
        system_prompt=system,
        user_prompt=user_prompt,
        model=TaskModel.SONNET,
        max_budget_usd=1.00,
        vault_root=ctx.vault_root,
        allowed_tools=[],
    )
    log.info(
        "orchestrate_research.llm_done",
        task_id=ctx.task_id,
        handle=payload.investigation_handle,
        finish_reason=result.finish_reason,
    )
    if result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=result, message="rate_limit")

    plan = _parse_plan(result.text) or _default_plan(payload.prompt)

    # Write investigation file
    inv_path = vc.investigation_path(ctx.vault_root, payload.investigation_handle)
    body = _investigation_body(payload, plan, memory_block)
    scope_type = str(plan.get("scope_type") or payload.scope_type or "crosscutting")
    memo_handle = (
        plan.get("final_artifact", {}).get("memo_handle")
        or f"research-{et_date_str().replace('-', '')}"
    )

    write_markdown_with_frontmatter(
        inv_path,
        body=body,
        metadata={
            "type": "investigation",
            "status": "active",
            "scope": scope_type,
            "initiated_by": "research_query",
            "hypothesis": plan.get("hypothesis", "")[:500],
            "entry_nodes": payload.entry_nodes
            or ([f"themes/{n.get('slug')}" for n in plan.get("theme_nodes", [])]),
            "created_at": et_iso(),
            "memo_handle": memo_handle,
            "tags": ["investigation", "open-ended", f"scope:{scope_type}"],
        },
    )

    # Ensure Investigation DB row + enqueue phase tasks under its id
    async def _ensure_and_enqueue(s) -> None:
        inv = (
            await s.execute(
                select(Investigation).where(
                    Investigation.handle == payload.investigation_handle
                )
            )
        ).scalar_one_or_none()
        if inv is None:
            inv = Investigation(
                handle=payload.investigation_handle,
                status="active",
                scope=scope_type,
                initiated_by="research_query",
                hypothesis=(plan.get("hypothesis") or payload.prompt)[:500],
                entry_nodes=payload.entry_nodes
                or [f"themes/{n.get('slug')}" for n in plan.get("theme_nodes", [])],
                vault_path=str(inv_path.relative_to(ctx.vault_root)),
                research_priority=payload.research_priority,
            )
            s.add(inv)
            await s.flush()

        await _enqueue_phase_tasks(
            s, inv, payload, plan, memo_handle
        )
        await touch_investigation(s, inv.id)

    if ctx.session is not None:
        await _ensure_and_enqueue(ctx.session)
    else:
        async with session_scope() as session:
            await _ensure_and_enqueue(session)

    log.info(
        "orchestrate_research.done",
        task_id=ctx.task_id,
        handle=payload.investigation_handle,
        scope=scope_type,
        themes=len(plan.get("theme_nodes", [])),
        questions=len(plan.get("question_nodes", [])),
        candidates=len(plan.get("candidate_tickers", [])),
    )
    return HandlerResult(ok=True, llm_result=result)


async def _enqueue_phase_tasks(
    s, inv: Investigation, payload: OrchestrateResearchPayload, plan: dict, memo_handle: str
) -> None:
    """Fan out retrieval → compile → answer → screen → synthesize."""
    ih = payload.investigation_handle
    subject = plan.get("subject") or payload.prompt[:160]
    priority = 2  # research lives in the dive lane priority tier
    queries = [q for q in plan.get("retrieval_queries", []) if isinstance(q, str) and q.strip()][:6]
    theme_slugs = [
        n.get("slug") for n in plan.get("theme_nodes", []) if isinstance(n, dict) and n.get("slug")
    ]
    concept_slugs = [
        n.get("slug") for n in plan.get("concept_nodes", []) if isinstance(n, dict) and n.get("slug")
    ]
    question_slugs = [
        n.get("slug") for n in plan.get("question_nodes", []) if isinstance(n, dict) and n.get("slug")
    ]
    candidate_tickers = [t for t in plan.get("candidate_tickers", []) if isinstance(t, str)][:15]

    # 1. Retrieval
    if queries:
        await enqueue_task(
            s,
            task_type=TaskType.GATHER_SOURCES,
            payload={
                "investigation_handle": ih,
                "subject": subject,
                "queries": queries,
                "related_nodes": [f"themes/{s_}" for s_ in theme_slugs]
                + [f"questions/{s_}" for s_ in question_slugs],
                "max_sources": 8,
            },
            priority=priority,
            dedup_key=f"gather:{ih}",
            investigation_id=inv.id,
        )

    # 2. Compile theme / concept nodes
    for slug in theme_slugs:
        await enqueue_task(
            s,
            task_type=TaskType.COMPILE_RESEARCH_NODE,
            payload={
                "investigation_handle": ih,
                "node_type": "theme",
                "node_slug": slug,
                "subject": subject,
                "source_paths": [],
                "related_nodes": [f"questions/{s_}" for s_ in question_slugs],
                "tickers": candidate_tickers,
            },
            priority=priority,
            dedup_key=f"compile_node:theme:{slug}:{ih}",
            investigation_id=inv.id,
        )
    for slug in concept_slugs:
        await enqueue_task(
            s,
            task_type=TaskType.COMPILE_RESEARCH_NODE,
            payload={
                "investigation_handle": ih,
                "node_type": "concept",
                "node_slug": slug,
                "subject": subject,
                "source_paths": [],
                "related_nodes": [f"themes/{s_}" for s_ in theme_slugs],
                "tickers": candidate_tickers,
            },
            priority=priority,
            dedup_key=f"compile_node:concept:{slug}:{ih}",
            investigation_id=inv.id,
        )

    # 3. Question nodes — compile stub, then answer
    for slug in question_slugs:
        await enqueue_task(
            s,
            task_type=TaskType.COMPILE_RESEARCH_NODE,
            payload={
                "investigation_handle": ih,
                "node_type": "question",
                "node_slug": slug,
                "subject": subject,
                "source_paths": [],
                "related_nodes": [f"themes/{s_}" for s_ in theme_slugs],
                "tickers": candidate_tickers,
            },
            priority=priority,
            dedup_key=f"compile_node:question:{slug}:{ih}",
            investigation_id=inv.id,
        )
        await enqueue_task(
            s,
            task_type=TaskType.ANSWER_QUESTION,
            payload={
                "investigation_handle": ih,
                "question_slug": slug,
                "research_priority": payload.research_priority,
            },
            priority=priority,
            dedup_key=f"answer:{slug}:{ih}",
            investigation_id=inv.id,
        )

    # 4. Candidate screening (if applicable)
    if candidate_tickers:
        await enqueue_task(
            s,
            task_type=TaskType.SCREEN_CANDIDATE_COMPANIES,
            payload={
                "investigation_handle": ih,
                "subject": subject,
                "tickers": candidate_tickers,
                "ranking_question": (
                    plan.get("hypothesis")
                    or f"Which of these tickers are most exposed to: {subject}?"
                )[:400],
                "max_deep_dives": 3,
            },
            priority=priority,
            dedup_key=f"screen:{ih}",
            investigation_id=inv.id,
        )

    # 5. Final cross-cut memo (terminal — gated by siblings via handler logic)
    await enqueue_task(
        s,
        task_type=TaskType.SYNTHESIZE_CROSSCUT_MEMO,
        payload={
            "investigation_handle": ih,
            "memo_handle": memo_handle,
            "subject": subject,
            "themes": theme_slugs,
            "concepts": concept_slugs,
            "questions": question_slugs,
            "tickers": candidate_tickers,
        },
        priority=priority,
        dedup_key=f"crosscut_memo:{ih}",
        investigation_id=inv.id,
    )
