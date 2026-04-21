from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from praxis_core.config import get_settings
from praxis_core.db.models import Heartbeat, Task
from praxis_core.db.session import session_scope
from praxis_core.llm.invoker import require_claude_cli
from praxis_core.logging import configure_logging, get_logger
from praxis_core.observability.events import emit_event
from praxis_core.observability.heartbeat import beat, stale_components
from praxis_core.observability.sd_notify import notify_ready, notify_stopping, notify_watchdog
from praxis_core.schemas.task_types import TaskType
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.time_et import now_et, now_utc
from services.scheduler.alerts import send_alert

log = get_logger("scheduler.main")


@dataclass
class CadenceJob:
    name: str
    interval_s: int
    action: Callable[[AsyncSession], Awaitable[None]]
    last_run: datetime | None = None
    consecutive_failures: int = 0
    last_error: str | None = None

    def due(self, now: datetime) -> bool:
        if self.last_run is None:
            return True
        return (now - self.last_run).total_seconds() >= self.interval_s


JOB_FAILURE_ALERT_THRESHOLD = 3


def _mark_job_success(job: CadenceJob) -> None:
    job.consecutive_failures = 0
    job.last_error = None


def _mark_job_failure(job: CadenceJob, error: Exception) -> None:
    job.consecutive_failures += 1
    job.last_error = str(error)[:200]


def _job_failure_alerts(jobs: list[CadenceJob]) -> list[str]:
    alerts: list[str] = []
    for job in jobs:
        if job.consecutive_failures >= JOB_FAILURE_ALERT_THRESHOLD:
            alerts.append(
                f"Scheduler job failing repeatedly: {job.name} "
                f"({job.consecutive_failures} consecutive failures). "
                f"last_error={job.last_error or 'unknown'}"
            )
    return alerts


async def _enqueue_refresh_index(session: AsyncSession) -> None:
    await enqueue_task(
        session,
        task_type=TaskType.REFRESH_INDEX,
        payload={"scope": "incremental", "triggered_by": "scheduler"},
        priority=4,
        dedup_key=f"refresh_index:{now_et().strftime('%Y%m%d%H')}",
    )


async def _enqueue_lint_vault(session: AsyncSession) -> None:
    await enqueue_task(
        session,
        task_type=TaskType.LINT_VAULT,
        payload={"triggered_by": "scheduler"},
        priority=4,
        dedup_key=f"lint_vault:{now_et().strftime('%Y%m%d')}",
    )


async def _enqueue_daily_journal(session: AsyncSession) -> None:
    # Daily journal runs at end of ET day; "yesterday" is the previous ET calendar date.
    yesterday = (now_et() - timedelta(days=1)).strftime("%Y-%m-%d")
    await enqueue_task(
        session,
        task_type=TaskType.GENERATE_DAILY_JOURNAL,
        payload={"date": yesterday, "triggered_by": "scheduler"},
        priority=4,
        dedup_key=f"generate_daily_journal:{yesterday}",
    )


async def _enqueue_cleanup_sessions(session: AsyncSession) -> None:
    await enqueue_task(
        session,
        task_type=TaskType.CLEANUP_SESSIONS,
        payload={"min_age_hours": 24, "triggered_by": "scheduler"},
        priority=4,
        dedup_key=f"cleanup_sessions:{now_et().strftime('%Y%m%d')}",
    )


async def _enqueue_surface_ideas(session: AsyncSession) -> None:
    """Section D D45 — 24/7 surface every 30min."""
    await enqueue_task(
        session,
        task_type=TaskType.SURFACE_IDEAS,
        payload={"triggered_by": "scheduler"},
        priority=3,
        dedup_key=f"surface_ideas:{now_et().strftime('%Y%m%d%H%M')[:11]}",
    )


async def _enqueue_refresh_backlinks(session: AsyncSession) -> None:
    """Wiki connectivity refresh — rewalk the graph, update managed
    `## Backlinks` sections on every theme/concept/people/question/
    investigation. Pure Python, no LLM. Every 4 hours."""
    await enqueue_task(
        session,
        task_type=TaskType.REFRESH_BACKLINKS,
        payload={"triggered_by": "scheduler"},
        priority=4,
        dedup_key=f"refresh_backlinks:{now_et().strftime('%Y%m%d%H')}",
    )


async def _enqueue_ticker_index(session: AsyncSession) -> None:
    """Orphan-ticker resolver — for every ticker with _analyzed/ data
    but no companies/<T>/ dir, create a minimal index.md graph stub.
    Idempotent; cheap enough to run hourly."""
    await enqueue_task(
        session,
        task_type=TaskType.TICKER_INDEX,
        payload={"triggered_by": "scheduler"},
        priority=4,
        dedup_key=f"ticker_index:{now_et().strftime('%Y%m%d%H')}",
    )


JOBS: list[CadenceJob] = [
    # Section D D39 — bumped from hourly to every 15min during market hours
    CadenceJob(name="refresh_index", interval_s=900, action=_enqueue_refresh_index),
    CadenceJob(name="lint_vault", interval_s=86400, action=_enqueue_lint_vault),
    CadenceJob(name="daily_journal", interval_s=86400, action=_enqueue_daily_journal),
    CadenceJob(name="cleanup_sessions", interval_s=86400, action=_enqueue_cleanup_sessions),
    # Surface ideas runs every 15 min — it's the engine of auto-dispatch
    # (high-urgency single-ticker ideas → orchestrate_dive), so the faster
    # we scan for new patterns, the sooner spare worker capacity gets
    # filled with research. Raw cost is ~$0.30/call (Sonnet) → ~$30/day
    # at 15min cadence, acceptable for the "always-busy analyst" property.
    CadenceJob(name="surface_ideas", interval_s=900, action=_enqueue_surface_ideas),
    # Wiki-connectivity refresh — graph traversal + orphan resolver
    CadenceJob(name="refresh_backlinks", interval_s=14400, action=_enqueue_refresh_backlinks),
    CadenceJob(name="ticker_index", interval_s=3600, action=_enqueue_ticker_index),
]


def _is_market_hours() -> bool:
    settings = get_settings()
    now = now_et()
    if now.weekday() >= 5:
        return False
    open_h, open_m = [int(x) for x in settings.market_open_et.split(":")]
    close_h, close_m = [int(x) for x in settings.market_close_et.split(":")]
    open_t = now.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
    close_t = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
    return open_t <= now <= close_t


async def _check_dead_man(session: AsyncSession) -> list[str]:
    """Returns list of alert messages (if any)."""
    alerts: list[str] = []
    now = now_utc()

    # Heartbeat staleness
    stale = await stale_components(session, stale_after_s=300)
    for component, _last, age in stale:
        if component == "scheduler.main":
            continue
        alerts.append(f"Heartbeat stale: {component} ({age}s old)")

    # No successful analyze_filing in 2hrs during market hours
    if _is_market_hours():
        cutoff = now - timedelta(hours=2)
        count = (
            await session.execute(
                select(func.count(Task.id))
                .where(Task.type == TaskType.ANALYZE_FILING.value)
                .where(Task.status == "success")
                .where(Task.finished_at >= cutoff)
            )
        ).scalar_one()

        # Also check that EDGAR poller is beating
        edgar_hb = (
            await session.execute(
                select(Heartbeat.last_heartbeat).where(Heartbeat.component == "pollers.edgar_8k")
            )
        ).scalar_one_or_none()
        edgar_recent = edgar_hb is not None and (now - edgar_hb).total_seconds() < 600

        if count == 0 and edgar_recent:
            alerts.append(
                "No successful analyze_filing in 2hrs during market hours (EDGAR still polling)"
            )

    # Dispatcher heartbeat older than 2min
    disp_hb = (
        await session.execute(
            select(Heartbeat.last_heartbeat).where(Heartbeat.component == "dispatcher.main")
        )
    ).scalar_one_or_none()
    if disp_hb is None or (now - disp_hb).total_seconds() > 120:
        alerts.append("Dispatcher not heartbeating (may be down)")

    return alerts


_last_alert_fingerprints: dict[str, datetime] = {}


def _should_alert(fingerprint: str, cooldown_s: int = 900) -> bool:
    now = now_utc()
    last = _last_alert_fingerprints.get(fingerprint)
    if last is None or (now - last).total_seconds() > cooldown_s:
        _last_alert_fingerprints[fingerprint] = now
        return True
    return False


async def run_loop() -> None:
    configure_logging()
    settings = get_settings()
    if settings.praxis_invoker == "cli":
        claude_path = require_claude_cli()
    else:
        claude_path = None
    log.info("scheduler.start", invoker=settings.praxis_invoker, claude_path=claude_path)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    await emit_event("scheduler.main", "started", {})
    notify_ready()

    while not stop_event.is_set():
        now = now_utc()

        # Cadence jobs
        async with session_scope() as session:
            for job in JOBS:
                if job.due(now):
                    try:
                        await job.action(session)
                        job.last_run = now
                        _mark_job_success(job)
                        log.info("scheduler.job_enqueued", job=job.name)
                    except Exception as e:
                        _mark_job_failure(job, e)
                        log.warning("scheduler.job_fail", job=job.name, error=str(e))

        # Dead-man's switch
        try:
            async with session_scope() as session:
                alerts = await _check_dead_man(session)
            alerts.extend(_job_failure_alerts(JOBS))
            for msg in alerts:
                fingerprint = msg.split(":")[0][:80]
                if _should_alert(fingerprint):
                    try:
                        await send_alert(title="praxis alert", body=msg, priority="high")
                        await emit_event("scheduler.main", "alert_fired", {"body": msg})
                    except Exception as e:
                        log.warning("scheduler.alert_fail", error=str(e), msg=msg[:200])
        except Exception as e:
            log.exception("scheduler.dead_man_fail", error=str(e))

        from praxis_core.time_et import et_iso

        notify_watchdog()
        await beat(
            "scheduler.main",
            status={
                "last_tick_at": et_iso(now),
                "jobs": len(JOBS),
                "job_failures": {j.name: j.consecutive_failures for j in JOBS},
            },
        )

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except TimeoutError:
            pass

    notify_stopping()
    log.info("scheduler.shutdown")


def main() -> None:
    asyncio.run(run_loop())


if __name__ == "__main__":
    main()
