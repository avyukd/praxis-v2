from __future__ import annotations

import shutil
import time

from handlers import HandlerContext, HandlerResult
from praxis_core.config import get_settings
from praxis_core.logging import get_logger
from praxis_core.schemas.payloads import CleanupSessionsPayload

log = get_logger("handlers.cleanup_sessions")


async def handle(ctx: HandlerContext) -> HandlerResult:
    settings = get_settings()
    root = settings.claude_sessions_root

    payload = CleanupSessionsPayload.model_validate(ctx.payload)
    cutoff = time.time() - (payload.min_age_hours * 3600)

    if not root.exists():
        return HandlerResult(ok=True, message="no sessions dir")

    removed = 0
    kept = 0
    errors = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if not child.name.startswith("session-"):
            continue
        try:
            mtime = child.stat().st_mtime
            if mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
            else:
                kept += 1
        except OSError as e:
            log.debug("cleanup_sessions.stat_fail", path=str(child), error=str(e))
            errors += 1

    log.info(
        "cleanup_sessions.done",
        removed=removed,
        kept=kept,
        errors=errors,
        min_age_hours=payload.min_age_hours,
    )
    return HandlerResult(
        ok=True,
        message=f"removed {removed}, kept {kept}",
    )
