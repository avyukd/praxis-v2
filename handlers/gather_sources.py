"""gather_sources — autonomous web retrieval with vault persistence."""

from __future__ import annotations

from handlers import HandlerContext, HandlerResult
from handlers._common import run_llm
from handlers.prompts.research_handlers import GATHER_SOURCES_PROMPT
from praxis_core.logging import get_logger
from praxis_core.schemas.payloads import GatherSourcesPayload
from praxis_core.schemas.task_types import TaskModel
from praxis_core.tasks.investigations import touch_investigation
from praxis_core.vault.constitution import constitution_prompt_block
from praxis_core.vault.steering import recent_steering

log = get_logger("handlers.gather_sources")


GATHER_ALLOWED_TOOLS = [
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "Bash(curl:*)",
    "Bash(mkdir:*)",
    # MCP tools the prompt tells the worker to call.
    "mcp__praxis__persist_source",
    "mcp__praxis__search_vault",
]


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = GatherSourcesPayload.model_validate(ctx.payload)

    constitution = constitution_prompt_block(ctx.vault_root)
    steering = recent_steering(ctx.vault_root, max_entries=6)
    system = GATHER_SOURCES_PROMPT + (
        ("\n\n" + constitution) if constitution else ""
    )

    queries_block = "\n".join(f"- {q}" for q in payload.queries)
    related_block = "\n".join(f"- [[{n}]]" for n in payload.related_nodes) or "(none)"
    parts = [
        "GATHER SOURCES",
        "",
        f"**Investigation:** {payload.investigation_handle}",
        f"**Subject:** {payload.subject}",
        f"**Max sources to persist:** {payload.max_sources}",
        "",
        "## Queries",
        queries_block,
        "",
        "## Related nodes",
        related_block,
    ]
    if steering:
        parts.extend(["", steering])
    user_prompt = "\n".join(parts)

    result = await run_llm(
        system_prompt=system,
        user_prompt=user_prompt,
        model=TaskModel.SONNET,
        max_budget_usd=1.50,
        vault_root=ctx.vault_root,
        allowed_tools=GATHER_ALLOWED_TOOLS,
    )
    log.info(
        "gather_sources.done",
        task_id=ctx.task_id,
        handle=payload.investigation_handle,
        finish_reason=result.finish_reason,
    )
    if result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=result, message="rate_limit")
    if ctx.session is not None:
        from sqlalchemy import select
        from praxis_core.db.models import Investigation

        inv = (
            await ctx.session.execute(
                select(Investigation).where(
                    Investigation.handle == payload.investigation_handle
                )
            )
        ).scalar_one_or_none()
        if inv is not None:
            await touch_investigation(ctx.session, inv.id)
    return HandlerResult(ok=True, llm_result=result)
