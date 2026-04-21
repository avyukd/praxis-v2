from __future__ import annotations

from services.scheduler.main import (
    JOB_FAILURE_ALERT_THRESHOLD,
    CadenceJob,
    _job_failure_alerts,
    _mark_job_failure,
    _mark_job_success,
)


async def _noop(_session):  # type: ignore[no-untyped-def]
    return None


def test_mark_job_failure_tracks_count_and_error():
    job = CadenceJob(name="surface_ideas", interval_s=60, action=_noop)
    _mark_job_failure(job, RuntimeError("boom"))
    assert job.consecutive_failures == 1
    assert "boom" in (job.last_error or "")


def test_mark_job_success_resets_failure_state():
    job = CadenceJob(name="refresh_index", interval_s=60, action=_noop)
    _mark_job_failure(job, RuntimeError("boom"))
    assert job.consecutive_failures == 1
    _mark_job_success(job)
    assert job.consecutive_failures == 0
    assert job.last_error is None


def test_job_failure_alerts_fire_at_threshold():
    job = CadenceJob(name="ticker_index", interval_s=60, action=_noop)
    for _ in range(JOB_FAILURE_ALERT_THRESHOLD - 1):
        _mark_job_failure(job, RuntimeError("still failing"))
    assert _job_failure_alerts([job]) == []
    _mark_job_failure(job, RuntimeError("still failing"))
    alerts = _job_failure_alerts([job])
    assert len(alerts) == 1
    assert "ticker_index" in alerts[0]
