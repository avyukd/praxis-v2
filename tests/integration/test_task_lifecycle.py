from __future__ import annotations

import pytest
from sqlalchemy import text

from praxis_core.db.models import Task
from praxis_core.schemas.artifacts import ValidationResult
from praxis_core.schemas.task_types import TaskType
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.tasks.lifecycle import (
    claim_next_task,
    mark_dead_letter,
    mark_success,
    requeue_on_rate_limit,
)


@pytest.mark.asyncio
async def test_enqueue_and_claim(db_session) -> None:
    tid = await enqueue_task(
        db_session,
        task_type=TaskType.TRIAGE_FILING,
        payload={
            "accession": "0001045810-26-000047",
            "form_type": "8-K",
            "ticker": "NVDA",
            "cik": "0001045810",
            "filing_url": "https://x",
            "raw_path": "_raw/filings/8-k/0001045810-26-000047/filing.txt",
        },
        priority=0,
        dedup_key="triage:NVDA-8K-47",
    )
    assert tid is not None
    await db_session.commit()

    t = await claim_next_task(db_session, worker_id="w-1")
    assert t is not None
    assert t.type == TaskType.TRIAGE_FILING.value
    assert t.status == "running"
    assert t.attempts == 1


@pytest.mark.asyncio
async def test_enqueue_dedup(db_session) -> None:
    payload = {
        "accession": "A",
        "form_type": "8-K",
        "ticker": "AAPL",
        "cik": "0000320193",
        "filing_url": "https://x",
        "raw_path": "_raw/filings/8-k/A/filing.txt",
    }
    t1 = await enqueue_task(
        db_session,
        task_type=TaskType.TRIAGE_FILING,
        payload=payload,
        priority=0,
        dedup_key="dedup-A",
    )
    await db_session.commit()
    t2 = await enqueue_task(
        db_session,
        task_type=TaskType.TRIAGE_FILING,
        payload=payload,
        priority=0,
        dedup_key="dedup-A",
    )
    await db_session.commit()
    assert t1 is not None
    assert t2 is None


@pytest.mark.asyncio
async def test_resource_lock_blocks_second_claim(db_session) -> None:
    # Two compile tasks on same ticker — should only claim one at a time
    for i in range(2):
        await enqueue_task(
            db_session,
            task_type=TaskType.COMPILE_TO_WIKI,
            payload={
                "source_kind": "filing_analysis",
                "analysis_path": f"_analyzed/filings/8-k/acc-{i}/analysis.md",
                "ticker": "NVDA",
                "accession": f"acc-{i}",
            },
            priority=0,
            dedup_key=f"compile:NVDA:{i}",
        )
    await db_session.commit()

    t1 = await claim_next_task(db_session, worker_id="w-1")
    assert t1 is not None
    assert t1.resource_key == "company:NVDA"

    # Second claim with the first's resource excluded — no task should be available
    t2 = await claim_next_task(db_session, worker_id="w-2", excluded_resource_keys=["company:NVDA"])
    assert t2 is None


@pytest.mark.asyncio
async def test_lease_expires_allows_reclaim(db_session) -> None:
    await enqueue_task(
        db_session,
        task_type=TaskType.TRIAGE_FILING,
        payload={
            "accession": "B",
            "form_type": "8-K",
            "ticker": "AAPL",
            "cik": "0",
            "filing_url": "x",
            "raw_path": "x",
        },
        priority=0,
        dedup_key="lease-B",
    )
    await db_session.commit()

    t = await claim_next_task(db_session, worker_id="w-1")
    await db_session.commit()
    assert t is not None

    # Force lease to expire
    await db_session.execute(
        text("UPDATE tasks SET lease_expires_at = now() - interval '1 hour' WHERE id = :id"),
        {"id": t.id},
    )
    await db_session.commit()

    t2 = await claim_next_task(db_session, worker_id="w-2")
    assert t2 is not None
    assert t2.id == t.id
    assert t2.attempts == 2


@pytest.mark.asyncio
async def test_mark_success_and_retry(db_session) -> None:
    await enqueue_task(
        db_session,
        task_type=TaskType.TRIAGE_FILING,
        payload={
            "accession": "C",
            "form_type": "8-K",
            "ticker": "AAPL",
            "cik": "0",
            "filing_url": "x",
            "raw_path": "x",
        },
        priority=0,
        dedup_key="success-C",
    )
    await db_session.commit()
    t = await claim_next_task(db_session, worker_id="w-1")
    await db_session.commit()
    await mark_success(
        db_session,
        t.id,
        validation=ValidationResult(ok=["a", "b"]),
        telemetry={"model": "haiku"},
    )
    await db_session.commit()
    await db_session.refresh(t)
    assert t.status == "success"
    assert t.validation_result["ok"] == ["a", "b"]


@pytest.mark.asyncio
async def test_dead_letter_on_max_attempts(db_session) -> None:
    tid = await enqueue_task(
        db_session,
        task_type=TaskType.TRIAGE_FILING,
        payload={
            "accession": "D",
            "form_type": "8-K",
            "ticker": "AAPL",
            "cik": "0",
            "filing_url": "x",
            "raw_path": "x",
        },
        priority=0,
        dedup_key="dl-D",
        max_attempts=2,
    )
    await db_session.commit()

    # Simulate attempts==max via direct update
    await db_session.execute(text("UPDATE tasks SET attempts = 2 WHERE id = :id"), {"id": tid})
    await mark_dead_letter(db_session, tid, "exhausted")
    await db_session.commit()

    t = await db_session.get(Task, tid)
    assert t is not None
    assert t.status == "dead_letter"


@pytest.mark.asyncio
async def test_requeue_on_rate_limit_does_not_count_as_attempt(db_session) -> None:
    await enqueue_task(
        db_session,
        task_type=TaskType.TRIAGE_FILING,
        payload={
            "accession": "E",
            "form_type": "8-K",
            "ticker": "AAPL",
            "cik": "0",
            "filing_url": "x",
            "raw_path": "x",
        },
        priority=0,
        dedup_key="rl-E",
    )
    await db_session.commit()
    t = await claim_next_task(db_session, worker_id="w-1")
    await db_session.commit()
    assert t.attempts == 1

    await requeue_on_rate_limit(db_session, t.id)
    await db_session.commit()

    await db_session.refresh(t)
    assert t.status == "queued"
    assert t.attempts == 0
    assert t.rate_limit_bounces == 1


@pytest.mark.asyncio
async def test_priority_and_age_ordering(db_session) -> None:
    # Enqueue a P4 older task and a P0 newer one — P0 newer should still win
    # but an old P4 should eventually beat a P2 (via age bump)
    # For simplicity, just test priority ordering here
    await enqueue_task(
        db_session,
        task_type=TaskType.LINT_VAULT,
        payload={"triggered_by": "test"},
        priority=4,
        dedup_key="lint",
    )
    await enqueue_task(
        db_session,
        task_type=TaskType.TRIAGE_FILING,
        payload={
            "accession": "F",
            "form_type": "8-K",
            "ticker": "NVDA",
            "cik": "0",
            "filing_url": "x",
            "raw_path": "x",
        },
        priority=0,
        dedup_key="priority-F",
    )
    await db_session.commit()

    t = await claim_next_task(db_session, worker_id="w-1")
    assert t is not None
    assert t.type == TaskType.TRIAGE_FILING.value  # higher priority wins
