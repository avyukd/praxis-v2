"""answer_question — resolve one research subquestion."""

from __future__ import annotations

from handlers import HandlerContext, HandlerResult
from handlers._common import run_llm
from handlers.prompts.research_handlers import ANSWER_QUESTION_PROMPT
from praxis_core.logging import get_logger
from praxis_core.schemas.payloads import AnswerQuestionPayload
from praxis_core.schemas.task_types import TaskModel
from praxis_core.vault.constitution import constitution_prompt_block

log = get_logger("handlers.answer_question")


ANSWER_ALLOWED_TOOLS = [
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "Bash(curl:*)",
    "mcp__praxis__search_vault",
]


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = AnswerQuestionPayload.model_validate(ctx.payload)
    question_path = ctx.vault_root / "questions" / f"{payload.question_slug}.md"
    if not question_path.exists():
        return HandlerResult(
            ok=False,
            message=f"question file missing: questions/{payload.question_slug}.md",
        )

    constitution = constitution_prompt_block(ctx.vault_root)
    system = ANSWER_QUESTION_PROMPT + (
        ("\n\n" + constitution) if constitution else ""
    )
    user_prompt = (
        "ANSWER QUESTION\n\n"
        f"**Investigation:** {payload.investigation_handle}\n"
        f"**Question file:** {question_path}\n"
        f"**Research priority:** {payload.research_priority}/10\n\n"
        "Read the question file, gather evidence, update the Answer + "
        "frontmatter status per the schema. Use Edit for in-place updates."
    )

    # Budget scales with priority — cheap research = quick answer, deep
    # research gets more room.
    usd_budget = 0.30 + (payload.research_priority / 10.0) * 1.20  # 0.30–1.50
    result = await run_llm(
        system_prompt=system,
        user_prompt=user_prompt,
        model=TaskModel.SONNET,
        max_budget_usd=usd_budget,
        vault_root=ctx.vault_root,
        allowed_tools=ANSWER_ALLOWED_TOOLS,
    )
    log.info(
        "answer_question.done",
        task_id=ctx.task_id,
        slug=payload.question_slug,
        finish_reason=result.finish_reason,
    )
    if result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=result, message="rate_limit")
    return HandlerResult(ok=True, llm_result=result)
