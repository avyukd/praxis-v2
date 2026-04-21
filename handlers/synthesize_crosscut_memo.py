"""synthesize_crosscut_memo — Opus-driven final memo for open-ended research.

Gated: if any required sibling task (gather_sources / compile / answer /
screen / child deep dives) is still in-flight, returns transient=True
for cooperative retry. Same pattern as synthesize_memo for company
deep dives.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from handlers import HandlerContext, HandlerResult
from handlers._common import run_llm
from handlers.prompts.research_handlers import SYNTHESIZE_CROSSCUT_MEMO_PROMPT
from praxis_core.db.models import Investigation, Task
from praxis_core.db.session import session_scope
from praxis_core.logging import get_logger
from praxis_core.schemas.payloads import SynthesizeCrosscutMemoPayload
from praxis_core.schemas.task_types import TaskModel
from praxis_core.time_et import now_utc
from praxis_core.vault import conventions as vc
from praxis_core.vault.constitution import constitution_prompt_block
from praxis_core.vault.steering import recent_steering

log = get_logger("handlers.synthesize_crosscut_memo")


CROSSCUT_ALLOWED_TOOLS = [
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash(mkdir:*)",
    "mcp__praxis__search_vault",
]


# Same wall-clock cap philosophy as synthesize_memo: don't wait forever
# for a sibling task that crashed. 4h is generous for broad-topic work.
_SIBLINGS_WALLCLOCK_CAP_H = 4


async def _siblings_blocking(session, investigation_handle: str) -> tuple[list[str], bool]:
    """Return (unfinished task types, timeout_exceeded).

    Checks all tasks for this investigation handle. If any non-memo task
    is still queued/running/partial, returns them. timeout_exceeded is
    True if the oldest unfinished task is > cap-hours old.
    """
    inv = (
        await session.execute(
            select(Investigation).where(Investigation.handle == investigation_handle)
        )
    ).scalar_one_or_none()
    if inv is None:
        return ([], False)

    rows = (
        await session.execute(
            select(Task.type, Task.status, Task.created_at).where(
                Task.investigation_id == inv.id
            )
        )
    ).all()
    unfinished_types: list[str] = []
    oldest_unfinished = None
    for r in rows:
        if r.type in ("synthesize_crosscut_memo",):
            continue  # skip self
        if r.status in ("queued", "running", "partial"):
            unfinished_types.append(r.type)
            if oldest_unfinished is None or r.created_at < oldest_unfinished:
                oldest_unfinished = r.created_at
    timeout_exceeded = False
    if oldest_unfinished is not None:
        age = now_utc() - oldest_unfinished
        if age > timedelta(hours=_SIBLINGS_WALLCLOCK_CAP_H):
            timeout_exceeded = True
    return (unfinished_types, timeout_exceeded)


def _memo_path(vault_root: Path, memo_handle: str) -> Path:
    from praxis_core.time_et import et_date_str

    stem = f"{et_date_str()}-{memo_handle}"
    return vault_root / "memos" / f"{stem}.md"


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = SynthesizeCrosscutMemoPayload.model_validate(ctx.payload)

    # Gate on sibling task completion
    async def _check(s) -> tuple[list[str], bool]:
        return await _siblings_blocking(s, payload.investigation_handle)

    if ctx.session is not None:
        unfinished, timeout = await _check(ctx.session)
    else:
        async with session_scope() as session:
            unfinished, timeout = await _check(session)

    if unfinished and not timeout:
        log.info(
            "synthesize_crosscut_memo.siblings_pending",
            handle=payload.investigation_handle,
            pending=unfinished,
        )
        return HandlerResult(
            ok=False,
            transient=True,
            message=f"waiting on siblings: {sorted(set(unfinished))}",
        )

    if unfinished and timeout:
        log.warning(
            "synthesize_crosscut_memo.sibling_timeout",
            handle=payload.investigation_handle,
            pending=unfinished,
        )

    memo_path = _memo_path(ctx.vault_root, payload.memo_handle)
    memo_path.parent.mkdir(parents=True, exist_ok=True)

    constitution = constitution_prompt_block(ctx.vault_root)
    steering = recent_steering(ctx.vault_root, max_entries=8)
    system = SYNTHESIZE_CROSSCUT_MEMO_PROMPT + (
        ("\n\n" + constitution) if constitution else ""
    )

    inv_path = vc.investigation_path(ctx.vault_root, payload.investigation_handle)

    parts = [
        "SYNTHESIZE CROSSCUT MEMO",
        "",
        f"**Investigation:** {payload.investigation_handle}",
        f"**Memo output path:** {memo_path}",
        f"**Subject:** {payload.subject}",
        "",
        f"**Investigation file to read first:** {inv_path}",
        "",
        f"**Themes:** {', '.join(payload.themes) or '(none)'}",
        f"**Concepts:** {', '.join(payload.concepts) or '(none)'}",
        f"**Questions:** {', '.join(payload.questions) or '(none)'}",
        f"**Candidate tickers:** {', '.join(payload.tickers) or '(none)'}",
    ]
    if unfinished and timeout:
        parts.extend(
            [
                "",
                "⚠️  **Sibling timeout** — some research tasks never completed:",
                f"    {sorted(set(unfinished))}. Draft the memo with what's available "
                "and mark status=draft with explicit gaps.",
            ]
        )
    if steering:
        parts.extend(["", steering])
    parts.append("")
    parts.append(
        "Read the investigation file + every referenced theme/question/concept/"
        "source/company memo, then write the memo per the schema. Use Write "
        "(not Edit) since the memo is a new file."
    )
    user_prompt = "\n".join(parts)

    result = await run_llm(
        system_prompt=system,
        user_prompt=user_prompt,
        model=TaskModel.OPUS,
        max_budget_usd=6.00,
        vault_root=ctx.vault_root,
        allowed_tools=CROSSCUT_ALLOWED_TOOLS,
    )
    log.info(
        "synthesize_crosscut_memo.done",
        task_id=ctx.task_id,
        handle=payload.investigation_handle,
        memo_handle=payload.memo_handle,
        finish_reason=result.finish_reason,
    )
    if result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=result, message="rate_limit")
    return HandlerResult(ok=True, llm_result=result)
