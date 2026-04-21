"""Tests for the non-deterministic surface_ideas mode system."""

from __future__ import annotations

from collections import Counter

import pytest

from handlers.surface_ideas import (
    MODE_WEIGHTS,
    _pick_mode,
    _wrap_user_prompt,
)


def test_all_modes_are_reachable_with_default_weights():
    """Over 5000 picks, every configured mode should show up at least once."""
    seen: Counter[str] = Counter()
    for _ in range(5000):
        seen[_pick_mode()] += 1
    for mode, _weight in MODE_WEIGHTS:
        assert seen[mode] > 0, f"{mode} never picked in 5000 samples"


def test_weight_ordering_roughly_respected():
    """recent_signals (weight 40) should dominate random_exploration (10) by ~4x."""
    n = 20_000
    seen: Counter[str] = Counter()
    for _ in range(n):
        seen[_pick_mode()] += 1
    # Generous bounds — this is random; 3x ratio is the floor for a sanity check.
    assert seen["recent_signals"] > seen["random_exploration"] * 3


def test_pick_mode_respects_available_filter():
    """If only a subset is available, the picker stays within it."""
    allowed = {"question_pursuit", "stale_coverage"}
    for _ in range(200):
        assert _pick_mode(available_modes=allowed) in allowed


def test_pick_mode_empty_available_falls_back():
    """Empty available set returns the hard default."""
    assert _pick_mode(available_modes=set()) == "recent_signals"


def test_pick_mode_single_available():
    """One allowed mode should always return that mode."""
    for _ in range(20):
        assert _pick_mode(available_modes={"theme_deepening"}) == "theme_deepening"


@pytest.mark.parametrize(
    "mode",
    [m for m, _ in MODE_WEIGHTS],
)
def test_wrap_user_prompt_prefixes_mode_label(mode):
    prompt = _wrap_user_prompt(
        mode=mode, body="Body text", steering="", focus="", constitution=""
    )
    assert prompt.startswith(f"SURFACE IDEAS (mode: {mode})")


def test_wrap_user_prompt_order_is_const_steering_focus_body():
    """Constitution comes before steering before focus before body."""
    prompt = _wrap_user_prompt(
        mode="recent_signals",
        body="BODY_MARKER",
        steering="STEERING_MARKER",
        focus="FOCUS_MARKER",
        constitution="CONSTITUTION_MARKER",
    )
    idx_const = prompt.index("CONSTITUTION_MARKER")
    idx_steer = prompt.index("STEERING_MARKER")
    idx_focus = prompt.index("FOCUS_MARKER")
    idx_body = prompt.index("BODY_MARKER")
    assert idx_const < idx_steer < idx_focus < idx_body


def test_wrap_user_prompt_omits_empty_sections():
    """No constitution, no steering, no focus → just mode header + body."""
    prompt = _wrap_user_prompt(
        mode="random_exploration",
        body="BODY",
        steering="",
        focus="",
        constitution="",
    )
    assert "SURFACE IDEAS" in prompt
    assert "BODY" in prompt
    assert "Operator steering" not in prompt
    assert "Focus hint" not in prompt
    assert "Analyst constitution" not in prompt


def test_mode_weights_is_nonempty_and_total_positive():
    """Config sanity — if someone empties MODE_WEIGHTS the picker degrades quietly."""
    assert MODE_WEIGHTS, "MODE_WEIGHTS must not be empty"
    assert sum(w for _, w in MODE_WEIGHTS) > 0
