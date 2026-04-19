from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from praxis_core.config import get_settings
from praxis_core.logging import get_logger

log = get_logger("llm.rate_limit")


def compute_backoff_seconds(consecutive_hits: int) -> int:
    """Exponential backoff in seconds.

    Hit #1: randomized between 180-300s.
    Hit #2: 900s (15 min).
    Hit #3: 1800s (30 min).
    Hit #4+: 3600s (60 min, capped).
    """
    settings = get_settings()
    if consecutive_hits <= 1:
        return random.randint(settings.rate_limit_initial_backoff_s_min, settings.rate_limit_initial_backoff_s_max)
    schedule = [900, 1800, 3600]
    idx = min(consecutive_hits - 2, len(schedule) - 1)
    return min(schedule[idx], settings.rate_limit_max_backoff_s)


@dataclass
class RateLimitSnapshot:
    status: str
    limited_until_ts: datetime | None
    consecutive_hits: int
    last_hit_ts: datetime | None
    probe_task_id: uuid.UUID | None


class RateLimitManager:
    """State machine for rate-limit tracking.

    Singleton row in rate_limit_state table. All operations are atomic
    within the session — caller is responsible for commit.

    States:
      - 'clear'    : dispatcher dispatches normally
      - 'limited'  : no dispatch until limited_until_ts
      - 'probing'  : exactly one synthetic probe task in flight; no other dispatch
    """

    async def snapshot(self, session: AsyncSession) -> RateLimitSnapshot:
        row = (
            await session.execute(
                text(
                    "SELECT status, limited_until_ts, consecutive_hits, last_hit_ts, probe_task_id "
                    "FROM rate_limit_state WHERE id = 1"
                )
            )
        ).one()
        return RateLimitSnapshot(
            status=row.status,
            limited_until_ts=row.limited_until_ts,
            consecutive_hits=row.consecutive_hits,
            last_hit_ts=row.last_hit_ts,
            probe_task_id=row.probe_task_id,
        )

    async def record_hit(self, session: AsyncSession) -> bool:
        """Called by a worker that just got rate-limited.

        Idempotent within 30s — if another worker already recorded a hit,
        this is a no-op. Returns True if *this* call was the recording writer.
        """
        result = await session.execute(
            text(
                """
                UPDATE rate_limit_state
                SET consecutive_hits = consecutive_hits + 1,
                    limited_until_ts = now() + (:backoff_s || ' seconds')::interval,
                    last_hit_ts = now(),
                    status = 'limited',
                    probe_task_id = NULL
                WHERE id = 1
                  AND (status != 'limited' OR last_hit_ts IS NULL
                       OR last_hit_ts < now() - interval '30 seconds')
                RETURNING consecutive_hits, limited_until_ts
                """
            ),
            {"backoff_s": compute_backoff_seconds(await self._peek_hits(session) + 1)},
        )
        row = result.first()
        if row is None:
            log.info("rate_limit.hit_ignored_dedup")
            return False
        log.warning(
            "rate_limit.hit_recorded",
            consecutive_hits=row.consecutive_hits,
            limited_until_ts=row.limited_until_ts.isoformat() if row.limited_until_ts else None,
        )
        return True

    async def _peek_hits(self, session: AsyncSession) -> int:
        row = (
            await session.execute(
                text("SELECT consecutive_hits FROM rate_limit_state WHERE id = 1")
            )
        ).one()
        return int(row.consecutive_hits)

    async def try_transition_to_probing(
        self, session: AsyncSession, probe_task_id: uuid.UUID
    ) -> bool:
        """Atomic CAS from 'limited' + expired to 'probing'.

        Returns True if transition occurred (dispatcher should enqueue a probe task).
        """
        result = await session.execute(
            text(
                """
                UPDATE rate_limit_state
                SET status = 'probing',
                    probe_task_id = :probe_task_id
                WHERE id = 1
                  AND status = 'limited'
                  AND limited_until_ts IS NOT NULL
                  AND limited_until_ts <= now()
                RETURNING status
                """
            ),
            {"probe_task_id": probe_task_id},
        )
        success = result.first() is not None
        if success:
            log.info("rate_limit.transition_probing", probe_task_id=str(probe_task_id))
        return success

    async def probe_succeeded(self, session: AsyncSession) -> None:
        await session.execute(
            text(
                """
                UPDATE rate_limit_state
                SET status = 'clear',
                    consecutive_hits = 0,
                    limited_until_ts = NULL,
                    probe_task_id = NULL
                WHERE id = 1
                """
            )
        )
        log.info("rate_limit.cleared_via_probe")

    async def reset_consecutive_hits(self, session: AsyncSession) -> None:
        """Called whenever any task succeeds — keeps consecutive_hits from
        ballooning across unrelated future outages."""
        await session.execute(
            text(
                """
                UPDATE rate_limit_state
                SET consecutive_hits = 0
                WHERE id = 1 AND consecutive_hits > 0 AND status = 'clear'
                """
            )
        )

    async def manual_clear(self, session: AsyncSession) -> None:
        await session.execute(
            text(
                """
                UPDATE rate_limit_state
                SET status = 'clear',
                    consecutive_hits = 0,
                    limited_until_ts = NULL,
                    probe_task_id = NULL
                WHERE id = 1
                """
            )
        )
        log.warning("rate_limit.manual_clear")

    async def can_dispatch(self, session: AsyncSession) -> tuple[bool, RateLimitSnapshot]:
        """Returns (True, snapshot) if normal dispatch should proceed.

        (False, snapshot) means the dispatcher is in 'limited' or 'probing' phase
        and should not assign normal tasks (probe already assigned separately).
        """
        snap = await self.snapshot(session)
        if snap.status == "clear":
            return True, snap
        return False, snap
