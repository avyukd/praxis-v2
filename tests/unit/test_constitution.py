"""Tests for praxis_core.vault.constitution."""

from __future__ import annotations

import pytest

from praxis_core.vault.constitution import (
    append_principle,
    constitution_path,
    constitution_prompt_block,
    read_constitution,
    remove_principle,
    replace_constitution,
)


@pytest.fixture
def vault(tmp_path):
    return tmp_path


def test_read_empty_returns_empty_string(vault):
    assert read_constitution(vault) == ""


def test_prompt_block_empty_when_no_file(vault):
    assert constitution_prompt_block(vault) == ""


def test_append_creates_file_with_scaffold(vault):
    append_principle(vault, "Skip merger arb under 5% upside", section="What to skip")
    text = read_constitution(vault)
    assert "# Analyst constitution" in text
    assert "## What to skip" in text
    assert "- Skip merger arb under 5% upside" in text


def test_append_to_multiple_sections(vault):
    append_principle(vault, "Avoid biotech basket", section="What to skip")
    append_principle(vault, "Prefer micro-caps", section="What to favor")
    append_principle(vault, "Show downside", section="Style + conduct")
    text = read_constitution(vault)
    assert "- Avoid biotech basket" in text
    assert "- Prefer micro-caps" in text
    assert "- Show downside" in text


def test_append_creates_new_section_when_missing(vault):
    append_principle(vault, "Universe rule", section="Universe")
    text = read_constitution(vault)
    assert "## Universe" in text
    assert "- Universe rule" in text


def test_append_dedup_exact_match(vault):
    append_principle(vault, "Same rule", section="What to favor")
    append_principle(vault, "Same rule", section="What to favor")
    text = read_constitution(vault)
    assert text.count("- Same rule") == 1


def test_prompt_block_strips_scaffold_placeholders(vault):
    append_principle(vault, "Real principle", section="What to favor")
    block = constitution_prompt_block(vault)
    assert "Real principle" in block
    assert "(add principles here)" not in block


def test_prompt_block_has_injection_header(vault):
    append_principle(vault, "Test rule", section="What to skip")
    block = constitution_prompt_block(vault)
    assert "## Analyst constitution" in block
    assert "operator-curated" in block


def test_remove_principle_matches_substring_case_insensitive(vault):
    append_principle(vault, "Skip merger arb setups", section="What to skip")
    append_principle(vault, "Prefer micro-caps", section="What to favor")
    removed, _ = remove_principle(vault, "MERGER ARB")
    assert removed == 1
    text = read_constitution(vault)
    assert "merger arb" not in text.lower()
    assert "micro-caps" in text.lower()


def test_remove_principle_empty_substring_is_noop(vault):
    append_principle(vault, "Some rule", section="What to favor")
    removed, _ = remove_principle(vault, "   ")
    assert removed == 0
    assert "Some rule" in read_constitution(vault)


def test_replace_backs_up_previous_version(vault):
    append_principle(vault, "Original rule", section="What to favor")
    replace_constitution(vault, "# Completely new\n\n## Custom\n- Fresh start\n")
    text = read_constitution(vault)
    assert "Completely new" in text
    assert "Original rule" not in text
    backups = list((vault / "_analyst").glob("constitution.backup-*.md"))
    assert len(backups) == 1
    assert "Original rule" in backups[0].read_text()


def test_constitution_path_returns_expected_location(vault):
    p = constitution_path(vault)
    assert p == vault / "_analyst" / "constitution.md"
