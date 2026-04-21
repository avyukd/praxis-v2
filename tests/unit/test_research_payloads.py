"""Registry + shape tests for the 6 open-ended research payloads."""

from __future__ import annotations

import pytest

from praxis_core.schemas.payloads import (
    AnswerQuestionPayload,
    CompileResearchNodePayload,
    GatherSourcesPayload,
    OrchestrateResearchPayload,
    PAYLOAD_MODELS,
    ScreenCandidateCompaniesPayload,
    SynthesizeCrosscutMemoPayload,
)
from praxis_core.schemas.task_types import TaskType


RESEARCH_TASK_TYPES = {
    TaskType.ORCHESTRATE_RESEARCH,
    TaskType.GATHER_SOURCES,
    TaskType.COMPILE_RESEARCH_NODE,
    TaskType.ANSWER_QUESTION,
    TaskType.SCREEN_CANDIDATE_COMPANIES,
    TaskType.SYNTHESIZE_CROSSCUT_MEMO,
}


def test_every_research_task_type_has_payload_model():
    for t in RESEARCH_TASK_TYPES:
        assert t.value in PAYLOAD_MODELS, f"{t.value} missing from PAYLOAD_MODELS"


def test_full_task_type_payload_map_complete():
    missing = [t.value for t in TaskType if t.value not in PAYLOAD_MODELS]
    assert missing == [], f"unmapped types: {missing}"


def test_orchestrate_research_minimal():
    p = OrchestrateResearchPayload.model_validate(
        {"prompt": "research hormuz fertilizer", "investigation_handle": "h-1"}
    )
    assert p.research_priority == 5
    assert p.tickers == []


def test_orchestrate_research_rejects_missing_prompt():
    with pytest.raises(Exception):
        OrchestrateResearchPayload.model_validate({"investigation_handle": "h-1"})


def test_gather_sources_requires_queries():
    p = GatherSourcesPayload.model_validate(
        {"investigation_handle": "h", "subject": "hormuz", "queries": ["q1"]}
    )
    assert p.max_sources == 8


def test_compile_research_node_enforces_node_type_literal():
    with pytest.raises(Exception):
        CompileResearchNodePayload.model_validate(
            {
                "investigation_handle": "h",
                "node_type": "bogus",
                "node_slug": "x",
                "subject": "s",
            }
        )


def test_compile_research_node_accepts_valid_types():
    for nt in ("theme", "concept", "question", "basket"):
        p = CompileResearchNodePayload.model_validate(
            {
                "investigation_handle": "h",
                "node_type": nt,
                "node_slug": "x",
                "subject": "s",
            }
        )
        assert p.node_type == nt


def test_answer_question_priority_default():
    p = AnswerQuestionPayload.model_validate(
        {"investigation_handle": "h", "question_slug": "q"}
    )
    assert p.research_priority == 5


def test_screen_candidate_companies_default_cap():
    p = ScreenCandidateCompaniesPayload.model_validate(
        {
            "investigation_handle": "h",
            "subject": "x",
            "tickers": ["MOS", "CF"],
            "ranking_question": "which is best",
        }
    )
    assert p.max_deep_dives == 3


def test_synthesize_crosscut_memo_lists_default_empty():
    p = SynthesizeCrosscutMemoPayload.model_validate(
        {"investigation_handle": "h", "memo_handle": "m", "subject": "s"}
    )
    assert p.themes == p.concepts == p.questions == p.tickers == []
