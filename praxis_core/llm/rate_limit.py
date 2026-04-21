from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from praxis_core.config import get_settings
from praxis_core.logging import get_logger

log = get_logger("llm.rate_limit")

# Ceiling on any single backoff window when we have no upstream resets_at
# and the local schedule would wait longer. Probing costs ~1 Haiku call;
# cheaper to probe often than to oversleep when the window is actually open.
BACKOFF_HARD_CAP_S = 900  # 15 min

# When upstream gives us a real resets_at timestamp, clamp to this. A
# pathological 7-day resets_at shouldn't freeze the system — we'll still
# probe at least this often to re-verify.
UPSTREAM_RESETS_HARD_CAP_S = 1800  # 30 min


def compute_backoff_seconds(consecutive_hits: int) -> int:
    """Local fallback schedule when upstream `resets_at` is unknown.

    Compressed: probe often, cap at 15 min. Anthropic's rate_limit_event
    usually carries a real resets_at — this branch only fires when it
    doesn't (e.g. 429 surfaced via error text, not the structured event).

    Hit #1: 60-120s jittered.
    Hit #2: 180s (3 min).
    Hit #3: 300s (5 min).
    Hit #4: 600s (10 min).
    Hit #5+: 900s (15 min, capped).
    """
    settings = get_settings()
    if consecutive_hits <= 1:
        return random.randint(
            settings.rate_limit_initial_backoff_s_min, settings.rate_limit_initial_backoff_s_max
        )
    schedule = [180, 300, 600, 900]
    idx = min(consecutive_hits - 2, len(schedule) - 1)
    return min(schedule[idx], settings.rate_limit_max_backoff_s)


def compute_limited_until_seconds(
    consecutive_hits: int, upstream_resets_at: int | None = None
) -> int:
    """Return seconds from now until the next probe should fire.

    Prefers Anthropic's resets_at (unix seconds) when provided — that's
    the authoritative window-open moment. Falls back to local schedule.

    Clamped to [15s, UPSTREAM_RESETS_HARD_CAP_S]. 15s floor protects
    against resets_at being in the past (would immediately probe again
    and thrash). Upper clamp protects against ridiculous upstream values.
    """
    if upstream_resets_at is not None:
        wait_s = int(upstream_resets_at) - int(time.time())
        if wait_s < 15:
            wait_s = 15
        return min(wait_s, UPSTREAM_RESETS_HARD_CAP_S)
    return compute_backoff_seconds(consecutive_hits)


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

    async def record_hit(
        self, session: AsyncSession, *, upstream_resets_at: int | None = None
    ) -> bool:
        """Called by a worker that just got rate-limited.

        Idempotent within 30s — if another worker already recorded a hit,
        this is a no-op. Returns True if *this* call was the recording writer.

        `upstream_resets_at` is the Anthropic-supplied unix timestamp when
        the window re-opens (from rate_limit_event.resetsAt). When present,
        it's authoritative — we wait until exactly then instead of guessing.
        """
        backoff_s = compute_limited_until_seconds(
            await self._peek_hits(session) + 1, upstream_resets_at
        )
        result = await session.execute(
            text(
                """
                UPDATE rate_limit_state
                SET consecutive_hits = consecutive_hits + 1,
                    limited_until_ts = now() + :backoff_s * interval '1 second',
                    last_hit_ts = now(),
                    status = 'limited',
                    probe_task_id = NULL
                WHERE id = 1
                  AND (status != 'limited' OR last_hit_ts IS NULL
                       OR last_hit_ts < now() - interval '30 seconds')
                RETURNING consecutive_hits, limited_until_ts
                """
            ),
            {"backoff_s": backoff_s},
        )
        row = result.first()
        if row is None:
            log.info("rate_limit.hit_ignored_dedup")
            return False
        log.warning(
            "rate_limit.hit_recorded",
            consecutive_hits=row.consecutive_hits,
            limited_until_ts=row.limited_until_ts.isoformat() if row.limited_until_ts else None,
            source="upstream_resets_at" if upstream_resets_at is not None else "local_schedule",
            wait_s=backoff_s,
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
