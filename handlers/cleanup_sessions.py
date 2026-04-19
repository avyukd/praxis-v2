from __future__ import annotations

import shutil
import time

from handlers import HandlerContext, HandlerResult
from praxis_core.config import get_settings
from praxis_core.logging import get_logger

log = get_logger("handlers.cleanup_sessions")

# Keep session dirs for 24h by default — plenty of time to inspect a tick's artifacts
# if something goes wrong. Configurable via task payload.
DEFAULT_MIN_AGE_HOURS = 24


async def handle(ctx: HandlerContext) -> HandlerResult:
    settings = get_settings()
    root = settings.claude_sessions_root

    min_age_hours = int(ctx.payload.get("min_age_hours", DEFAULT_MIN_AGE_HOURS))
    cutoff = time.time() - (min_age_hours * 3600)

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
        min_age_hours=min_age_hours,
    )
    return HandlerResult(
        ok=True,
        message=f"removed {removed}, kept {kept}",
    )
