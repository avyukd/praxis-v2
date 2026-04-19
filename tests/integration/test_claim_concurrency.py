from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from praxis_core.db.models import Base
from praxis_core.schemas.task_types import TaskType
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.tasks.lifecycle import claim_next_task


def _has_postgres() -> bool:
    return bool(os.environ.get("PRAXIS_TEST_DATABASE_URL"))


@pytest.mark.asyncio
async def test_concurrent_claims_never_double_claim() -> None:
    """Four concurrent claim_next_task calls against a single queued task must
    produce exactly one claimer. Tests FOR UPDATE SKIP LOCKED semantics."""
    if not _has_postgres():
        pytest.skip("PRAXIS_TEST_DATABASE_URL not set")

    url = os.environ["PRAXIS_TEST_DATABASE_URL"]
    engine = create_async_engine(url, pool_pre_ping=True, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        from sqlalchemy import text

        await conn.execute(
            text(
                "INSERT INTO rate_limit_state (id, status) VALUES (1, 'clear') "
                "ON CONFLICT DO NOTHING"
            )
        )

    sm = async_sessionmaker(engine, expire_on_commit=False)

    # Seed one task
    async with sm() as s:
        await enqueue_task(
            s,
            task_type=TaskType.TRIAGE_FILING,
            payload={
                "accession": "CONTEND",
                "form_type": "8-K",
                "ticker": "NVDA",
                "cik": "0",
                "filing_url": "x",
                "raw_path": "x",
            },
            priority=0,
            dedup_key="contend:1",
        )
        await s.commit()

    async def one_claimer(wid: str):
        async with sm() as s:
            t = await claim_next_task(s, worker_id=wid)
            await s.commit()
            return t

    results = await asyncio.gather(*(one_claimer(f"w-{i}") for i in range(4)))
    claimed = [r for r in results if r is not None]
    assert len(claimed) == 1, f"expected exactly 1 claim, got {len(claimed)}"
    assert claimed[0].type == TaskType.TRIAGE_FILING.value

    await engine.dispose()


@pytest.mark.asyncio
async def test_age_bump_promotes_old_low_priority_task() -> None:
    """Task in tier P2 aged > age_bump_after_min should beat a fresh P1 (P2-1=P1 tie,
    then created_at tie-break — old P2 wins)."""
    if not _has_postgres():
        pytest.skip("PRAXIS_TEST_DATABASE_URL not set")

    url = os.environ["PRAXIS_TEST_DATABASE_URL"]
    engine = create_async_engine(url, pool_pre_ping=True, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        from sqlalchemy import text

        await conn.execute(
            text(
                "INSERT INTO rate_limit_state (id, status) VALUES (1, 'clear') "
                "ON CONFLICT DO NOTHING"
            )
        )

    sm = async_sessionmaker(engine, expire_on_commit=False)

    async with sm() as s:
        old_p2 = await enqueue_task(
            s,
            task_type=TaskType.DIVE_BUSINESS,
            payload={"ticker": "NVDA", "investigation_handle": "inv-1"},
            priority=2,
            dedup_key="old-p2",
        )
        from sqlalchemy import text

        # Backdate the P2 task to 45 min ago (age_bump_after_min default is 30)
        await s.execute(
            text("UPDATE tasks SET created_at = now() - interval '45 minutes' WHERE id = :id"),
            {"id": old_p2},
        )
        await enqueue_task(
            s,
            task_type=TaskType.ANALYZE_FILING,
            payload={
                "accession": "FRESH",
                "form_type": "8-K",
                "ticker": "NVDA",
                "cik": "0",
                "triage_result_path": "x",
                "raw_path": "x",
            },
            priority=1,
            dedup_key="fresh-p1",
        )
        await s.commit()

    async with sm() as s:
        t = await claim_next_task(s, worker_id="w-1")
        await s.commit()
    assert t is not None
    assert t.id == old_p2, f"expected aged P2 to win via age-bump tie with P1; got {t.type}"

    await engine.dispose()
