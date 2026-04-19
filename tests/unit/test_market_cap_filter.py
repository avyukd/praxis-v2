from __future__ import annotations

from praxis_core.filters.market_cap import passes_mcap_filter


def test_under_threshold_passes() -> None:
    assert passes_mcap_filter(1_500_000_000, max_usd=2_000_000_000) is True


def test_over_threshold_fails() -> None:
    assert passes_mcap_filter(5_000_000_000, max_usd=2_000_000_000) is False


def test_exact_threshold_passes() -> None:
    assert passes_mcap_filter(2_000_000_000, max_usd=2_000_000_000) is True


def test_unknown_defaults_to_pass() -> None:
    # We keep unknowns (small/obscure micro-caps we probably want to see).
    assert passes_mcap_filter(None, max_usd=2_000_000_000) is True


def test_unknown_with_strict_flag_drops() -> None:
    assert passes_mcap_filter(None, max_usd=2_000_000_000, keep_unknown=False) is False


def test_zero_mcap_passes() -> None:
    # 0 is valid — tiny shell company under any threshold.
    assert passes_mcap_filter(0, max_usd=2_000_000_000) is True
