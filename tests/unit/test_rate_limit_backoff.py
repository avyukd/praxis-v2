from __future__ import annotations

from praxis_core.llm.rate_limit import compute_backoff_seconds


def test_first_hit_randomized_range() -> None:
    values = {compute_backoff_seconds(1) for _ in range(200)}
    assert all(180 <= v <= 300 for v in values)
    assert len(values) > 1


def test_second_hit_15_min() -> None:
    assert compute_backoff_seconds(2) == 900


def test_third_hit_30_min() -> None:
    assert compute_backoff_seconds(3) == 1800


def test_fourth_plus_capped_60_min() -> None:
    assert compute_backoff_seconds(4) == 3600
    assert compute_backoff_seconds(10) == 3600
    assert compute_backoff_seconds(100) == 3600


def test_zero_hit_treated_as_one() -> None:
    v = compute_backoff_seconds(0)
    assert 180 <= v <= 300
