from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from praxis_core.llm.rate_limit import RateLimitManager


@pytest.mark.asyncio
async def test_record_hit_transitions_to_limited(db_session) -> None:
    rl = RateLimitManager()
    snap = await rl.snapshot(db_session)
    assert snap.status == "clear"
    assert snap.consecutive_hits == 0

    wrote = await rl.record_hit(db_session)
    await db_session.commit()
    assert wrote is True

    snap2 = await rl.snapshot(db_session)
    assert snap2.status == "limited"
    assert snap2.consecutive_hits == 1
    assert snap2.limited_until_ts is not None


@pytest.mark.asyncio
async def test_concurrent_hit_deduplication(db_session) -> None:
    rl = RateLimitManager()
    wrote1 = await rl.record_hit(db_session)
    await db_session.commit()
    wrote2 = await rl.record_hit(db_session)
    await db_session.commit()
    assert wrote1 is True
    assert wrote2 is False

    snap = await rl.snapshot(db_session)
    assert snap.consecutive_hits == 1


@pytest.mark.asyncio
async def test_transition_to_probing_requires_expired(db_session) -> None:
    rl = RateLimitManager()
    # Set state manually: limited, but future
    await db_session.execute(
        text(
            "UPDATE rate_limit_state SET status='limited', "
            "limited_until_ts = now() + interval '1 hour', consecutive_hits = 1 "
            "WHERE id=1"
        )
    )
    await db_session.commit()

    ok = await rl.try_transition_to_probing(db_session, uuid.uuid4())
    assert ok is False


@pytest.mark.asyncio
async def test_transition_to_probing_succeeds_when_expired(db_session) -> None:
    rl = RateLimitManager()
    await db_session.execute(
        text(
            "UPDATE rate_limit_state SET status='limited', "
            "limited_until_ts = now() - interval '1 second', consecutive_hits = 1 "
            "WHERE id=1"
        )
    )
    await db_session.commit()

    pid = uuid.uuid4()
    ok = await rl.try_transition_to_probing(db_session, pid)
    await db_session.commit()
    assert ok is True

    snap = await rl.snapshot(db_session)
    assert snap.status == "probing"
    assert snap.probe_task_id == pid


@pytest.mark.asyncio
async def test_probe_success_clears_state(db_session) -> None:
    rl = RateLimitManager()
    await db_session.execute(
        text(
            "UPDATE rate_limit_state SET status='probing', consecutive_hits=3 WHERE id=1"
        )
    )
    await db_session.commit()

    await rl.probe_succeeded(db_session)
    await db_session.commit()

    snap = await rl.snapshot(db_session)
    assert snap.status == "clear"
    assert snap.consecutive_hits == 0
    assert snap.limited_until_ts is None


@pytest.mark.asyncio
async def test_manual_clear(db_session) -> None:
    rl = RateLimitManager()
    await rl.record_hit(db_session)
    await db_session.commit()

    await rl.manual_clear(db_session)
    await db_session.commit()

    snap = await rl.snapshot(db_session)
    assert snap.status == "clear"
    assert snap.consecutive_hits == 0


@pytest.mark.asyncio
async def test_can_dispatch(db_session) -> None:
    rl = RateLimitManager()
    ok, snap = await rl.can_dispatch(db_session)
    assert ok is True
    assert snap.status == "clear"

    await rl.record_hit(db_session)
    await db_session.commit()

    ok2, snap2 = await rl.can_dispatch(db_session)
    assert ok2 is False
    assert snap2.status == "limited"
