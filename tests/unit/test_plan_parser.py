from __future__ import annotations

from handlers._plan_parser import parse_plan
from praxis_core.schemas.task_types import TaskType


def test_parses_numbered_plan() -> None:
    body = """
---
type: investigation
---

# Investigation: NVDA

## Plan
1. dive_financial_rigorous — always first, gate
2. dive_business_moat — understand segments
3. dive_industry_structure — cycle
4. synthesize_memo — crystallize the view

## Log
"""
    result = parse_plan(body)
    assert result == [
        TaskType.DIVE_FINANCIAL_RIGOROUS,
        TaskType.DIVE_BUSINESS_MOAT,
        TaskType.DIVE_INDUSTRY_STRUCTURE,
        TaskType.SYNTHESIZE_MEMO,
    ]


def test_respects_llm_ordering() -> None:
    body = """
## Plan
- dive_financial_rigorous first (gate)
- dive_capital_allocation second
- dive_macro third
- synthesize_memo last
"""
    result = parse_plan(body)
    assert result == [
        TaskType.DIVE_FINANCIAL_RIGOROUS,
        TaskType.DIVE_CAPITAL_ALLOCATION,
        TaskType.DIVE_MACRO,
        TaskType.SYNTHESIZE_MEMO,
    ]


def test_skips_unknown_task_types() -> None:
    body = """
## Plan
1. dive_business_moat
2. dive_nonexistent  (LLM hallucinated this)
3. dive_macro
"""
    result = parse_plan(body)
    assert result == [TaskType.DIVE_BUSINESS_MOAT, TaskType.DIVE_MACRO]


def test_dedupes_duplicate_mentions() -> None:
    body = """
## Plan
- dive_business_moat
- dive_business_moat — note: again for emphasis
- dive_business_moat
"""
    result = parse_plan(body)
    assert result == [TaskType.DIVE_BUSINESS_MOAT]


def test_no_plan_section_returns_empty() -> None:
    body = """
# Investigation

Some notes without a Plan section.
"""
    assert parse_plan(body) == []


def test_empty_input_returns_empty() -> None:
    assert parse_plan("") == []


def test_stops_at_next_heading() -> None:
    body = """
## Plan
- dive_business_moat

## Log
- dive_macro — this should NOT be picked up
"""
    result = parse_plan(body)
    assert result == [TaskType.DIVE_BUSINESS_MOAT]


def test_case_insensitive_plan_heading() -> None:
    body = """
## PLAN
- dive_financial_rigorous
"""
    assert parse_plan(body) == [TaskType.DIVE_FINANCIAL_RIGOROUS]


def test_custom_dive_recognized() -> None:
    body = """
## Plan
1. dive_financial_rigorous
2. dive_custom — specialty=uranium-market-specialist
3. synthesize_memo
"""
    result = parse_plan(body)
    assert result == [
        TaskType.DIVE_FINANCIAL_RIGOROUS,
        TaskType.DIVE_CUSTOM,
        TaskType.SYNTHESIZE_MEMO,
    ]
