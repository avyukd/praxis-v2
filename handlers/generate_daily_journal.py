from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from praxis_core.db.models import Event, SignalFired, Task
from praxis_core.db.session import session_scope
from praxis_core.logging import get_logger
from praxis_core.schemas.payloads import GenerateDailyJournalPayload
from praxis_core.vault import conventions as vc
from praxis_core.vault.writer import atomic_write

from handlers import HandlerContext, HandlerResult

log = get_logger("handlers.generate_daily_journal")


async def _summarize_day(session: AsyncSession, date_str: str) -> str:
    start = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    task_rows = (
        await session.execute(
            select(Task.type, Task.status, Task.started_at, Task.finished_at, Task.payload)
            .where(Task.started_at >= start)
            .where(Task.started_at < end)
            .order_by(Task.started_at)
        )
    ).all()

    signal_rows = (
        await session.execute(
            select(SignalFired.ticker, SignalFired.signal_type, SignalFired.urgency, SignalFired.payload)
            .where(SignalFired.fired_at >= start)
            .where(SignalFired.fired_at < end)
            .order_by(SignalFired.fired_at)
        )
    ).all()

    by_type: dict[str, dict[str, int]] = {}
    for r in task_rows:
        by_type.setdefault(r.type, {"success": 0, "failed": 0, "partial": 0, "other": 0})
        k = r.status if r.status in ("success", "failed", "partial") else "other"
        by_type[r.type][k] += 1

    parts = [
        f"# Daily journal — {date_str}",
        "",
        f"## Activity summary",
        "",
    ]
    if not by_type:
        parts.append("No task activity recorded.")
    else:
        parts.append("| Task type | Success | Partial | Failed | Other |")
        parts.append("|---|---|---|---|---|")
        for t, counts in sorted(by_type.items()):
            parts.append(
                f"| {t} | {counts['success']} | {counts['partial']} | {counts['failed']} | {counts['other']} |"
            )

    parts.extend(["", f"## Signals fired ({len(signal_rows)})", ""])
    if not signal_rows:
        parts.append("No signals fired.")
    else:
        for r in signal_rows:
            parts.append(
                f"- **{r.urgency}** `{r.signal_type}` {r.ticker or '-'} — "
                f"{(r.payload or {}).get('title', '')}"
            )

    return "\n".join(parts) + "\n"


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = GenerateDailyJournalPayload.model_validate(ctx.payload)
    async with session_scope() as session:
        body = await _summarize_day(session, payload.date)

    out_path = ctx.vault_root / "journal" / f"{payload.date}.md"
    atomic_write(out_path, body)
    log.info("generate_daily_journal.done", date=payload.date, path=str(out_path))
    return HandlerResult(ok=True, message=f"journal written: {out_path}")
