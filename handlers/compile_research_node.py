"""compile_research_node — non-company analogue of compile_to_wiki.

Writes/updates themes/, concepts/, or questions/ markdown files with
frontmatter + evidence section, drawing from persisted sources.
"""

from __future__ import annotations

from pathlib import Path

from handlers import HandlerContext, HandlerResult
from handlers._common import run_llm
from handlers.prompts.research_handlers import COMPILE_RESEARCH_NODE_PROMPT
from praxis_core.logging import get_logger
from praxis_core.schemas.payloads import CompileResearchNodePayload
from praxis_core.schemas.task_types import TaskModel
from praxis_core.time_et import et_iso
from praxis_core.vault.constitution import constitution_prompt_block
from praxis_core.vault.writer import write_markdown_with_frontmatter

log = get_logger("handlers.compile_research_node")


COMPILE_NODE_ALLOWED_TOOLS = [
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash(mkdir:*)",
    "mcp__praxis__search_vault",
]


def _node_path(vault_root: Path, node_type: str, slug: str) -> Path:
    if node_type == "theme":
        return vault_root / "themes" / f"{slug}.md"
    if node_type == "concept":
        return vault_root / "concepts" / f"{slug}.md"
    if node_type == "question":
        return vault_root / "questions" / f"{slug}.md"
    if node_type == "basket":
        return vault_root / "baskets" / f"{slug}.md"
    raise ValueError(f"unknown node_type {node_type!r}")


def _ensure_skeleton(
    path: Path, node_type: str, slug: str, subject: str, related_nodes: list[str]
) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if node_type == "theme":
        body = (
            f"# {slug.replace('-', ' ').title()}\n\n"
            f"**Subject:** {subject}\n\n"
            "## Thesis\n\n_In progress._\n\n"
            "## Channels of impact\n\n"
            "## Related industries\n\n"
            "## Linked commodities / macros\n\n"
            "## Evidence\n\n"
            "## Related nodes\n\n"
            + "\n".join(f"- [[{n}]]" for n in related_nodes)
            + "\n"
        )
        status = "active"
    elif node_type == "concept":
        body = (
            f"# {slug.replace('-', ' ').title()}\n\n"
            f"**Subject:** {subject}\n\n"
            "## Definition\n\n_In progress._\n\n"
            "## Mechanism\n\n"
            "## Where it shows up\n\n"
            "## Related nodes\n\n"
            + "\n".join(f"- [[{n}]]" for n in related_nodes)
            + "\n"
        )
        status = "evergreen"
    elif node_type == "question":
        body = (
            f"# {slug.replace('-', ' ').capitalize()}?\n\n"
            f"**Subject:** {subject}\n\n"
            "## Why it matters\n\n_In progress._\n\n"
            "## What would answer it\n\n"
            "## Answer\n\n_Not yet answered._\n\n"
            "## Evidence\n\n"
            "## Related nodes\n\n"
            + "\n".join(f"- [[{n}]]" for n in related_nodes)
            + "\n"
        )
        status = "open"
    else:
        body = f"# {slug}\n\n_Stub._\n"
        status = "active"
    write_markdown_with_frontmatter(
        path,
        body=body,
        metadata={
            "type": node_type,
            "status": status,
            "subject": subject,
            "created_at": et_iso(),
            "tags": [node_type, "auto_generated"],
        },
    )


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = CompileResearchNodePayload.model_validate(ctx.payload)

    out_path = _node_path(ctx.vault_root, payload.node_type, payload.node_slug)
    _ensure_skeleton(
        out_path,
        payload.node_type,
        payload.node_slug,
        payload.subject,
        payload.related_nodes,
    )

    constitution = constitution_prompt_block(ctx.vault_root)
    system = COMPILE_RESEARCH_NODE_PROMPT + (
        ("\n\n" + constitution) if constitution else ""
    )

    source_block = (
        "\n".join(f"- [[{p}]]" for p in payload.source_paths)
        or "(no sources persisted yet — search the vault for nearby ones)"
    )
    related_block = (
        "\n".join(f"- [[{n}]]" for n in payload.related_nodes) or "(none)"
    )
    tickers_block = ", ".join(payload.tickers) or "(none)"

    user_prompt = (
        "COMPILE RESEARCH NODE\n\n"
        f"**Investigation:** {payload.investigation_handle}\n"
        f"**Node type:** {payload.node_type}\n"
        f"**Node slug:** {payload.node_slug}\n"
        f"**Subject:** {payload.subject}\n\n"
        f"**Target file (already exists with skeleton):** {out_path}\n\n"
        "## Sources\n"
        f"{source_block}\n\n"
        "## Related nodes\n"
        f"{related_block}\n\n"
        "## Candidate tickers (for linking)\n"
        f"{tickers_block}\n\n"
        "Update the node per the schema. Use the Edit tool; don't "
        "rewrite the whole file unless it's genuinely empty."
    )

    result = await run_llm(
        system_prompt=system,
        user_prompt=user_prompt,
        model=TaskModel.SONNET,
        max_budget_usd=0.80,
        vault_root=ctx.vault_root,
        allowed_tools=COMPILE_NODE_ALLOWED_TOOLS,
    )
    log.info(
        "compile_research_node.done",
        task_id=ctx.task_id,
        node_type=payload.node_type,
        slug=payload.node_slug,
        finish_reason=result.finish_reason,
    )
    if result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=result, message="rate_limit")
    return HandlerResult(ok=True, llm_result=result)
