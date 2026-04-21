from __future__ import annotations

import time
from unittest.mock import patch

from praxis_core.llm.rate_limit import (
    UPSTREAM_RESETS_HARD_CAP_S,
    compute_backoff_seconds,
    compute_limited_until_seconds,
)


def test_first_hit_randomized_range() -> None:
    values = {compute_backoff_seconds(1) for _ in range(200)}
    assert all(60 <= v <= 300 for v in values)
    assert len(values) > 1


def test_second_hit_3_min() -> None:
    assert compute_backoff_seconds(2) == 180


def test_third_hit_5_min() -> None:
    assert compute_backoff_seconds(3) == 300


def test_fourth_hit_10_min() -> None:
    assert compute_backoff_seconds(4) == 600


def test_fifth_plus_capped_15_min() -> None:
    assert compute_backoff_seconds(5) == 900
    assert compute_backoff_seconds(10) == 900
    assert compute_backoff_seconds(100) == 900


def test_zero_hit_treated_as_one() -> None:
    v = compute_backoff_seconds(0)
    assert 60 <= v <= 300


def test_upstream_resets_at_trusted_when_provided() -> None:
    """Anthropic-supplied reset timestamp is authoritative."""
    future = int(time.time()) + 240
    wait_s = compute_limited_until_seconds(1, upstream_resets_at=future)
    assert 235 <= wait_s <= 245


def test_upstream_resets_at_overrides_local_schedule() -> None:
    """Even if local says wait 900s, trust upstream's 120s."""
    future = int(time.time()) + 120
    wait_s = compute_limited_until_seconds(5, upstream_resets_at=future)
    assert 115 <= wait_s <= 125


def test_upstream_resets_at_in_past_floors_to_15s() -> None:
    """If resets_at has already passed (clock skew / stale event), don't
    probe instantly and thrash — wait at least 15s."""
    past = int(time.time()) - 60
    wait_s = compute_limited_until_seconds(1, upstream_resets_at=past)
    assert wait_s == 15


def test_upstream_resets_at_clamped_to_hard_cap() -> None:
    """Protect against pathological upstream values (e.g. 7-day reset)."""
    far_future = int(time.time()) + 7 * 24 * 3600
    wait_s = compute_limited_until_seconds(1, upstream_resets_at=far_future)
    assert wait_s == UPSTREAM_RESETS_HARD_CAP_S


def test_fallback_to_local_when_no_upstream() -> None:
    wait_s = compute_limited_until_seconds(2, upstream_resets_at=None)
    assert wait_s == 180
