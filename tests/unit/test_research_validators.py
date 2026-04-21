"""Validators for the 4 research tasks that gate success/partial."""

from __future__ import annotations

import pytest

from praxis_core.tasks.validators import (
    validate_answer_question,
    validate_compile_research_node,
    validate_orchestrate_research,
    validate_synthesize_crosscut_memo,
)


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "investigations").mkdir()
    (tmp_path / "themes").mkdir()
    (tmp_path / "questions").mkdir()
    (tmp_path / "concepts").mkdir()
    (tmp_path / "memos").mkdir()
    return tmp_path


def _write(p, content):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_orchestrate_research_missing_file_fails(vault):
    r = validate_orchestrate_research(
        {"prompt": "x", "investigation_handle": "h"}, vault
    )
    assert r.is_success is False
    assert r.missing


def test_orchestrate_research_requires_frontmatter(vault):
    _write(vault / "investigations" / "h.md", "no frontmatter here")
    r = validate_orchestrate_research(
        {"prompt": "x", "investigation_handle": "h"}, vault
    )
    assert r.is_success is False
    assert r.malformed


def test_orchestrate_research_happy_path(vault):
    _write(
        vault / "investigations" / "h.md",
        "---\ntype: investigation\nstatus: active\n---\n# Research\n",
    )
    r = validate_orchestrate_research(
        {"prompt": "x", "investigation_handle": "h"}, vault
    )
    assert r.is_success
    assert r.ok


def test_compile_research_node_requires_evidence_section(vault):
    _write(
        vault / "themes" / "hormuz.md",
        "---\ntype: theme\n---\n# Hormuz\n\n## Thesis\nx\n",
    )
    r = validate_compile_research_node(
        {
            "investigation_handle": "h",
            "node_type": "theme",
            "node_slug": "hormuz",
            "subject": "s",
        },
        vault,
    )
    assert r.is_success is False
    assert "Evidence" in r.malformed[0].reason


def test_compile_research_node_happy_path(vault):
    _write(
        vault / "questions" / "q.md",
        "---\ntype: question\nstatus: open\n---\n# Q\n\n## Evidence\n- [[x]]\n",
    )
    r = validate_compile_research_node(
        {
            "investigation_handle": "h",
            "node_type": "question",
            "node_slug": "q",
            "subject": "s",
        },
        vault,
    )
    assert r.is_success


def test_answer_question_open_without_answer_fails(vault):
    _write(
        vault / "questions" / "q.md",
        "---\ntype: question\nstatus: open\n---\n# Q\n\n## Answer\n\n_Not yet answered._\n",
    )
    r = validate_answer_question(
        {"investigation_handle": "h", "question_slug": "q"}, vault
    )
    assert r.is_success is False


def test_answer_question_answered_passes(vault):
    _write(
        vault / "questions" / "q.md",
        "---\ntype: question\nstatus: answered\n---\n# Q\n\n## Answer\n\nYes.\n",
    )
    r = validate_answer_question(
        {"investigation_handle": "h", "question_slug": "q"}, vault
    )
    assert r.is_success


def test_answer_question_partial_passes(vault):
    _write(
        vault / "questions" / "q.md",
        "---\ntype: question\nstatus: partial\n---\n# Q\n\n## Answer\n\nMaybe.\n\n## Gaps\n- missing X\n",
    )
    r = validate_answer_question(
        {"investigation_handle": "h", "question_slug": "q"}, vault
    )
    assert r.is_success


def test_synthesize_crosscut_memo_missing_file_fails(vault):
    r = validate_synthesize_crosscut_memo(
        {"investigation_handle": "h", "memo_handle": "m", "subject": "s"}, vault
    )
    assert r.is_success is False
    assert r.missing


def test_synthesize_crosscut_memo_requires_required_sections(vault):
    _write(
        vault / "memos" / "2026-04-21-m.md",
        "---\ntype: crosscut_memo\n---\n# Memo\n\n## Thesis\nok\n",
    )
    r = validate_synthesize_crosscut_memo(
        {"investigation_handle": "h", "memo_handle": "m", "subject": "s"}, vault
    )
    assert r.is_success is False
    assert "missing sections" in r.malformed[0].reason


def test_synthesize_crosscut_memo_happy_path(vault):
    _write(
        vault / "memos" / "2026-04-21-m.md",
        "---\ntype: crosscut_memo\nstatus: final\n---\n# M\n\n"
        "## Thesis\nok\n\n## Evidence\n- [[x]]\n\n## Equity ranking\n- A Buy\n\n"
        "## Known vs uncertain\n- known: A\n",
    )
    r = validate_synthesize_crosscut_memo(
        {"investigation_handle": "h", "memo_handle": "m", "subject": "s"}, vault
    )
    assert r.is_success
