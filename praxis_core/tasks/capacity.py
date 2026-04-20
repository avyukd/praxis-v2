"""Dispatcher capacity helper — is there room to schedule more work?

Used by idle-work enqueuers (surface_ideas → auto-investigation, potentially
a background sweep for low-priority tasks) to gate "nice-to-have" work when
live-path tasks (analyze_filing, compile_to_wiki, notify) should have
priority.

Two signals:
  1. Pool utilization — running tasks / pool_size. We target ≤80%.
  2. Rate-limit state — if Claude is currently throttled, hold.

Daily cap on auto-dispatch to prevent runaway loops: stored in
system_state under `surface_autodispatch.<YYYY-MM-DD>`.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from praxis_core.config import get_settings
from praxis_core.db.models import RateLimitState, Task


@dataclass
class CapacityReport:
    running: int
    pool_size: int
    utilization: float
    rate_limit_clear: bool
    at_capacity: bool  # True when we should hold off auto-dispatch
    spare_slots: int  # >=0 — how many more tasks would fit under 80% target


async def get_pool_capacity(session: AsyncSession) -> CapacityReport:
    """Read current running-task count + compute spare capacity vs 80% target."""
    settings = get_settings()
    pool_size = max(1, getattr(settings, "dispatcher_pool_size", 4))
    target_max = max(1, int(pool_size * 0.8))  # 80% of pool — floor 1

    running = (
        await session.execute(select(func.count(Task.id)).where(Task.status == "running"))
    ).scalar_one()
    rl = (await session.execute(select(RateLimitState).where(RateLimitState.id == 1))).scalar_one_or_none()
    rl_clear = rl is None or rl.status == "clear"

    utilization = running / pool_size if pool_size else 1.0
    at_cap = utilization >= 0.8 or not rl_clear
    spare = max(0, target_max - int(running))

    return CapacityReport(
        running=int(running),
        pool_size=pool_size,
        utilization=round(utilization, 2),
        rate_limit_clear=rl_clear,
        at_capacity=at_cap,
        spare_slots=spare,
    )
