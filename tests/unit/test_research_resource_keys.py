"""Tests for the resource-key extensions in praxis_core.tasks.enqueue
for research engine task types.

Two new key families:
- research_node: keyed on (node_type, slug) for compile/answer so
  two writers can't race on the same theme/question/concept file.
- crosscutting: keyed on investigation handle so only one crosscut
  memo writer runs per investigation.
"""

from __future__ import annotations

from praxis_core.schemas.task_types import TaskType
from praxis_core.tasks.enqueue import _resource_key_for


def test_orchestrate_research_keys_on_investigation():
    k = _resource_key_for(
        TaskType.ORCHESTRATE_RESEARCH,
        {"prompt": "x", "investigation_handle": "inv-1"},
    )
    assert k == "investigation:inv-1"


def test_gather_sources_has_no_resource_key():
    """Retrieval runs in parallel — key is intentionally None."""
    k = _resource_key_for(
        TaskType.GATHER_SOURCES,
        {"investigation_handle": "inv-1", "subject": "s", "queries": ["q"]},
    )
    assert k is None


def test_compile_research_node_theme_keys_on_theme_slug():
    k = _resource_key_for(
        TaskType.COMPILE_RESEARCH_NODE,
        {
            "investigation_handle": "inv-1",
            "node_type": "theme",
            "node_slug": "hormuz",
            "subject": "s",
        },
    )
    assert k == "theme:hormuz"


def test_compile_research_node_question_keys_on_question_slug():
    k = _resource_key_for(
        TaskType.COMPILE_RESEARCH_NODE,
        {
            "investigation_handle": "inv-1",
            "node_type": "question",
            "node_slug": "q1",
            "subject": "s",
        },
    )
    assert k == "question:q1"


def test_compile_research_node_concept_keys_on_concept_slug():
    k = _resource_key_for(
        TaskType.COMPILE_RESEARCH_NODE,
        {
            "investigation_handle": "inv-1",
            "node_type": "concept",
            "node_slug": "chokepoint-economics",
            "subject": "s",
        },
    )
    assert k == "concept:chokepoint-economics"


def test_answer_question_keys_on_question_slug():
    k = _resource_key_for(
        TaskType.ANSWER_QUESTION,
        {"investigation_handle": "inv-1", "question_slug": "q1"},
    )
    assert k == "question:q1"


def test_answer_question_falls_back_to_investigation_when_slug_missing():
    """Defensive fallback if payload omits slug (shouldn't happen, but)."""
    k = _resource_key_for(
        TaskType.ANSWER_QUESTION,
        {"investigation_handle": "inv-1"},
    )
    assert k == "investigation:inv-1"


def test_answer_question_none_when_both_missing():
    k = _resource_key_for(TaskType.ANSWER_QUESTION, {})
    assert k is None


def test_screen_candidate_companies_has_no_resource_key():
    """Screening is stateless-ish + fast — parallel OK."""
    k = _resource_key_for(
        TaskType.SCREEN_CANDIDATE_COMPANIES,
        {
            "investigation_handle": "inv-1",
            "subject": "s",
            "tickers": ["A"],
            "ranking_question": "q",
        },
    )
    assert k is None


def test_synthesize_crosscut_memo_keys_on_crosscutting_handle():
    """Only one memo writer per investigation."""
    k = _resource_key_for(
        TaskType.SYNTHESIZE_CROSSCUT_MEMO,
        {
            "investigation_handle": "inv-1",
            "memo_handle": "m",
            "subject": "s",
        },
    )
    assert k == "crosscutting:inv-1"


def test_synthesize_crosscut_memo_none_when_handle_missing():
    k = _resource_key_for(
        TaskType.SYNTHESIZE_CROSSCUT_MEMO,
        {"memo_handle": "m", "subject": "s"},
    )
    assert k is None


def test_different_node_types_with_same_slug_get_distinct_keys():
    """A theme named 'x' and a question named 'x' must not collide."""
    theme_key = _resource_key_for(
        TaskType.COMPILE_RESEARCH_NODE,
        {
            "investigation_handle": "inv-1",
            "node_type": "theme",
            "node_slug": "x",
            "subject": "s",
        },
    )
    question_key = _resource_key_for(
        TaskType.COMPILE_RESEARCH_NODE,
        {
            "investigation_handle": "inv-1",
            "node_type": "question",
            "node_slug": "x",
            "subject": "s",
        },
    )
    assert theme_key != question_key
