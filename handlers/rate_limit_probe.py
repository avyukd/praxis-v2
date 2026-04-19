from __future__ import annotations

from handlers import HandlerContext, HandlerResult
from handlers._common import run_llm
from praxis_core.logging import get_logger
from praxis_core.schemas.task_types import TaskModel

log = get_logger("handlers.rate_limit_probe")


SYSTEM_PROMPT = "You are a minimal probe. Respond with exactly one word: 'ok'."


async def handle(ctx: HandlerContext) -> HandlerResult:
    """Synthetic ping to test rate-limit recovery.

    Success → dispatcher/worker pipeline calls rate_limiter.probe_succeeded().
    Rate-limit → stays in limited state, backoff increments.
    """
    result = await run_llm(
        system_prompt=SYSTEM_PROMPT,
        user_prompt="Reply with only 'ok'. Do not call any tools.",
        model=TaskModel.HAIKU,
        max_turns=1,
        vault_root=ctx.vault_root,
        allowed_tools=[],
    )
    log.info("rate_limit_probe.done", finish_reason=result.finish_reason)
    if result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=result, message="rate_limit")
    if result.finish_reason in ("stop", "max_turns"):
        return HandlerResult(ok=True, llm_result=result, message="probe_ok")
    return HandlerResult(ok=False, llm_result=result, message=f"probe_{result.finish_reason}")
