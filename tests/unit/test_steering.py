"""Tests for praxis_core.vault.steering — rolling operator nudges."""

from __future__ import annotations

import pytest

from praxis_core.vault.steering import append_steering, recent_steering, steering_path


@pytest.fixture
def vault(tmp_path):
    return tmp_path


def test_recent_empty_when_no_file(vault):
    assert recent_steering(vault) == ""


def test_append_creates_file_with_entry(vault):
    p = append_steering(vault, "Focus on micro-caps this week")
    assert p.exists()
    text = p.read_text()
    assert "Focus on micro-caps this week" in text
    assert "observer" in text  # author default


def test_append_preserves_previous_entries(vault):
    append_steering(vault, "First nudge")
    append_steering(vault, "Second nudge")
    text = steering_path(vault).read_text()
    assert "First nudge" in text
    assert "Second nudge" in text


def test_recent_returns_newest_first(vault):
    append_steering(vault, "Oldest nudge")
    append_steering(vault, "Newer nudge")
    append_steering(vault, "Newest nudge")
    rendered = recent_steering(vault, max_entries=10)
    newest_idx = rendered.find("Newest nudge")
    oldest_idx = rendered.find("Oldest nudge")
    assert newest_idx < oldest_idx  # newest appears first


def test_recent_caps_max_entries(vault):
    for i in range(5):
        append_steering(vault, f"Nudge {i}")
    rendered = recent_steering(vault, max_entries=2)
    # Should contain latest two (3, 4) but not 0, 1
    assert "Nudge 4" in rendered
    assert "Nudge 3" in rendered
    assert "Nudge 0" not in rendered


def test_recent_includes_prompt_header(vault):
    append_steering(vault, "Any nudge")
    rendered = recent_steering(vault)
    assert "Operator steering" in rendered


def test_custom_author_recorded(vault):
    append_steering(vault, "Automated note", author="smoke_test")
    text = steering_path(vault).read_text()
    assert "smoke_test" in text
