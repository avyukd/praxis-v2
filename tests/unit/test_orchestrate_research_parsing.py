"""Tests for the plan-parser + default-plan fallback in orchestrate_research."""

from __future__ import annotations

import json

from handlers.orchestrate_research import _default_plan, _parse_plan


def test_parse_plan_strips_code_fences():
    raw = (
        "```json\n"
        '{"scope_type": "theme", "subject": "x", "retrieval_queries": ["a"]}\n'
        "```"
    )
    plan = _parse_plan(raw)
    assert plan is not None
    assert plan["scope_type"] == "theme"


def test_parse_plan_accepts_bare_json():
    raw = '{"scope_type":"theme","subject":"x"}'
    assert _parse_plan(raw)["scope_type"] == "theme"


def test_parse_plan_returns_none_on_non_json_prose():
    assert _parse_plan("no json here just prose") is None


def test_parse_plan_returns_none_on_malformed_json():
    assert _parse_plan('{"scope_type": "theme"') is None


def test_parse_plan_tolerates_surrounding_text():
    raw = 'Here is the plan:\n\n{"scope_type": "basket", "candidate_tickers": ["MOS"]}\n\nNotes.'
    plan = _parse_plan(raw)
    assert plan["candidate_tickers"] == ["MOS"]


def test_default_plan_has_all_required_keys():
    p = _default_plan("research hormuz")
    for k in (
        "scope_type",
        "subject",
        "hypothesis",
        "theme_nodes",
        "question_nodes",
        "concept_nodes",
        "retrieval_queries",
        "candidate_tickers",
        "tickers_to_deep_dive",
        "final_artifact",
    ):
        assert k in p


def test_default_plan_preserves_prompt_as_retrieval_query():
    p = _default_plan("hormuz fertilizer research")
    assert p["retrieval_queries"] == ["hormuz fertilizer research"]
