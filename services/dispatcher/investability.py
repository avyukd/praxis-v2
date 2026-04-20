"""INVESTABILITY gate post-dive handler (Section B D20).

After `dive_financial_rigorous` succeeds, we read the output file and
extract the required trailing line:

    INVESTABILITY: CONTINUE — <reason>
    INVESTABILITY: STOP     — <reason>

- CONTINUE → no-op, other specialists keep running
- STOP     → cancel sibling queued dives in the same investigation,
             emit investability_stop event; synthesize_memo still runs
             and will produce a terse "Too Hard" memo
- missing/malformed → fail-open (treat as CONTINUE), emit
                      investability_malformed event for audit

Every decision (machine or human override) lands as an event for
auditability.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from praxis_core.db.models import Task
from praxis_core.logging import get_logger
from praxis_core.observability.events import emit_event

log = get_logger("dispatcher.investability")

INVESTABILITY_RE = re.compile(
    r"^INVESTABILITY:\s*(CONTINUE|STOP)\s*[—-]\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE
)

Decision = Literal["CONTINUE", "STOP", "MALFORMED"]


def parse_investability(content: str) -> tuple[Decision, str]:
    """Find the last INVESTABILITY line in the content.

    Returns (decision, reason). Decision is MALFORMED if no line is
    found (fail-open — caller treats as CONTINUE but logs an audit
    event).
    """
    matches = list(INVESTABILITY_RE.finditer(content or ""))
    if not matches:
        return ("MALFORMED", "no INVESTABILITY line found")
    last = matches[-1]
    decision = last.group(1).upper()
    reason = last.group(2).strip()
    if decision not in ("CONTINUE", "STOP"):
        return ("MALFORMED", f"unrecognized verdict {decision!r}")
    return (decision, reason)  # type: ignore[return-value]


async def cancel_sibling_dives(
    session: AsyncSession,
    investigation_id: uuid.UUID,
    reason: str,
    excluding_task_id: uuid.UUID,
) -> list[str]:
    """Cancel queued dives in this investigation (keeps running ones
    alone — they'll finish on their own path). Returns list of canceled
    task ids for logging.
    """
    result = await session.execute(
        text(
            """
            UPDATE tasks
            SET status = 'canceled',
                finished_at = now(),
                lease_holder = NULL,
                lease_expires_at = NULL,
                last_error = :err
            WHERE investigation_id = :inv
              AND id != :self_id
              AND status = 'queued'
              AND type LIKE 'dive_%'
            RETURNING id, type
            """
        ),
        {
            "inv": investigation_id,
            "self_id": excluding_task_id,
            "err": f"investability_stop: {reason}"[:500],
        },
    )
    canceled = [(str(row.id), row.type) for row in result]
    return [f"{tid}({ttype})" for tid, ttype in canceled]


async def handle_post_dive_investability(
    session: AsyncSession,
    task: Task,
    vault_root: Path,
) -> None:
    """Called after dive_financial_rigorous mark_success. Reads the
    output .md, parses INVESTABILITY, applies gate. Never raises —
    errors are logged + emitted as events but don't fail the task
    (it already succeeded)."""
    ticker = task.payload.get("ticker") if task.payload else None
    investigation_id = task.investigation_id
    investigation_handle = (
        task.payload.get("investigation_handle") if task.payload else None
    ) or None

    if not ticker:
        log.warning("investability.no_ticker", task_id=str(task.id))
        return

    out_path = vault_root / "companies" / ticker / "dives" / "financial-rigorous.md"
    try:
        content = out_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log.warning("investability.read_fail", path=str(out_path), error=str(e))
        return

    decision, reason = parse_investability(content)

    base_payload = {
        "task_id": str(task.id),
        "ticker": ticker,
        "investigation_id": str(investigation_id) if investigation_id else None,
        "investigation_handle": investigation_handle,
        "decision": decision,
        "reason": reason[:500],
    }

    if decision == "MALFORMED":
        await emit_event(
            "dispatcher.investability", "investability_malformed", base_payload
        )
        log.info("investability.malformed", **base_payload)
        return

    if decision == "CONTINUE":
        await emit_event(
            "dispatcher.investability", "investability_continue", base_payload
        )
        log.info("investability.continue", **base_payload)
        return

    # STOP
    canceled: list[str] = []
    if investigation_id is not None:
        canceled = await cancel_sibling_dives(
            session, investigation_id, reason, task.id
        )
    payload = {**base_payload, "canceled_siblings": canceled}
    await emit_event("dispatcher.investability", "investability_stop", payload)
    log.info("investability.stop", **payload)
