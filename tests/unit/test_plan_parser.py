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
1. dive_business — understand segments first
2. dive_moat — evaluate durability
3. dive_financials — 5yr trajectory
4. synthesize_memo — crystallize the view

## Log
"""
    result = parse_plan(body)
    assert result == [
        TaskType.DIVE_BUSINESS,
        TaskType.DIVE_MOAT,
        TaskType.DIVE_FINANCIALS,
        TaskType.SYNTHESIZE_MEMO,
    ]


def test_respects_llm_ordering() -> None:
    body = """
## Plan
- dive_financials first (understand numbers)
- dive_moat second
- dive_business third
- synthesize_memo last
"""
    result = parse_plan(body)
    assert result == [
        TaskType.DIVE_FINANCIALS,
        TaskType.DIVE_MOAT,
        TaskType.DIVE_BUSINESS,
        TaskType.SYNTHESIZE_MEMO,
    ]


def test_skips_unknown_task_types() -> None:
    body = """
## Plan
1. dive_business
2. dive_nonexistent  (LLM hallucinated this)
3. dive_moat
"""
    result = parse_plan(body)
    assert result == [TaskType.DIVE_BUSINESS, TaskType.DIVE_MOAT]


def test_dedupes_duplicate_mentions() -> None:
    body = """
## Plan
- dive_business
- dive_business — note: again for emphasis
- dive_moat
"""
    result = parse_plan(body)
    assert result == [TaskType.DIVE_BUSINESS, TaskType.DIVE_MOAT]


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
- dive_business

## Log
- dive_moat — this should NOT be picked up
"""
    result = parse_plan(body)
    assert result == [TaskType.DIVE_BUSINESS]


def test_case_insensitive_plan_heading() -> None:
    body = """
## PLAN
- dive_business
"""
    assert parse_plan(body) == [TaskType.DIVE_BUSINESS]
