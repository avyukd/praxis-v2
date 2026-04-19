"""Parse an investigation file's ## Plan section to extract ordered dive task names.

The orchestrator LLM writes a plan like:
    ## Plan
    1. dive_business — understand segments first
    2. dive_moat — then moat durability
    3. synthesize_memo — crystallize

We extract `["dive_business", "dive_moat", "synthesize_memo"]` in order, ignoring prose.
"""

from __future__ import annotations

import re

from praxis_core.schemas.task_types import TaskType

VALID_DIVE_TASK_TYPES: tuple[TaskType, ...] = (
    TaskType.DIVE_BUSINESS,
    TaskType.DIVE_MOAT,
    TaskType.DIVE_FINANCIALS,
    TaskType.SYNTHESIZE_MEMO,
)

_VALID_SET: set[str] = {t.value for t in VALID_DIVE_TASK_TYPES}


def parse_plan(markdown: str) -> list[TaskType]:
    """Returns the ordered list of task types the orchestrator's plan specifies.

    Extracts any known `dive_*` or `synthesize_memo` mention from the `## Plan` section,
    preserving the LLM's ordering. De-duplicates while preserving first occurrence.
    Returns empty list if no plan section found — caller should fall back to default.
    """
    if not markdown:
        return []

    # Locate the ## Plan section (stop at next ## heading or end of doc)
    plan_match = re.search(
        r"^##\s+Plan\b.*?$(.*?)(?=^##\s|\Z)",
        markdown,
        flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    if not plan_match:
        return []

    plan_body = plan_match.group(1)
    seen: set[str] = set()
    ordered: list[TaskType] = []
    for match in re.finditer(r"\b(dive_\w+|synthesize_memo)\b", plan_body):
        name = match.group(1)
        if name in _VALID_SET and name not in seen:
            seen.add(name)
            ordered.append(TaskType(name))
    return ordered
