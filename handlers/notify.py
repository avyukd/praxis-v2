from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential

from praxis_core.config import get_settings
from praxis_core.db.models import SignalFired
from praxis_core.db.session import session_scope
from praxis_core.logging import get_logger
from praxis_core.schemas.payloads import NotifyPayload
from praxis_core.vault.writer import append_atomic

from handlers import HandlerContext, HandlerResult

log = get_logger("handlers.notify")


@retry(wait=wait_exponential(multiplier=1, min=1, max=30), stop=stop_after_attempt(4))
def _push_ntfy(topic_url: str, title: str, body: str, priority: str = "default") -> None:
    with httpx.Client(timeout=10) as client:
        response = client.post(
            topic_url,
            content=body.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": "chart_with_upwards_trend",
            },
        )
        response.raise_for_status()


_URGENCY_TO_PRIORITY = {
    "low": "low",
    "medium": "default",
    "high": "high",
    "intraday": "urgent",
}


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = NotifyPayload.model_validate(ctx.payload)
    settings = get_settings()
    topic_url = f"{settings.ntfy_base_url.rstrip('/')}/{settings.ntfy_signal_topic}"

    body = payload.body
    if payload.linked_analysis_path:
        body += f"\n\n→ {payload.linked_analysis_path}"

    priority = _URGENCY_TO_PRIORITY.get(payload.urgency, "default")
    try:
        _push_ntfy(topic_url, title=payload.title, body=body, priority=priority)
    except Exception as e:
        log.warning("notify.ntfy_fail", error=str(e), topic=topic_url)
        return HandlerResult(ok=False, message=f"ntfy push failed: {e}")

    async with session_scope() as session:
        session.add(
            SignalFired(
                id=uuid.uuid4(),
                task_id=uuid.UUID(ctx.task_id),
                ticker=payload.ticker,
                signal_type=payload.signal_type,
                urgency=payload.urgency,
                payload={
                    "title": payload.title,
                    "body": payload.body,
                    "linked_analysis_path": payload.linked_analysis_path,
                },
            )
        )

    log_file = ctx.vault_root / "_analyzed" / "notify.log"
    line = (
        f"{datetime.now(timezone.utc).isoformat()} "
        f"{payload.urgency} {payload.signal_type} "
        f"{payload.ticker or '-'} {payload.title}\n"
    )
    try:
        append_atomic(log_file, line)
    except Exception as e:
        log.warning("notify.log_append_fail", error=str(e))

    log.info(
        "notify.pushed",
        ticker=payload.ticker,
        urgency=payload.urgency,
        signal_type=payload.signal_type,
    )
    return HandlerResult(ok=True, message="pushed")
