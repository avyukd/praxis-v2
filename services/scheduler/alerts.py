from __future__ import annotations

import asyncio
from typing import Literal

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from praxis_core.config import get_settings
from praxis_core.logging import get_logger

log = get_logger("scheduler.alerts")

Priority = Literal["low", "default", "high", "urgent"]


@retry(wait=wait_exponential(multiplier=1, min=1, max=15), stop=stop_after_attempt(3))
async def send_alert(
    title: str, body: str, priority: Priority = "high", tags: str = "warning"
) -> None:
    settings = get_settings()
    topic_url = f"{settings.ntfy_base_url.rstrip('/')}/{settings.ntfy_alert_topic}"
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            topic_url,
            content=body.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": tags},
        )
        response.raise_for_status()
    log.info("alert.sent", title=title, priority=priority)
