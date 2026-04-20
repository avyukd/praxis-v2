"""Parse an investigation file's ## Plan section to extract ordered dive task names.

The orchestrator LLM writes a plan like:
    ## Plan
    1. dive_financial_rigorous — always first, emits INVESTABILITY line
    2. dive_business_moat — understand business + moat
    3. dive_industry_structure — cycle + structural trends
    4. dive_custom specialty=uranium-market-specialist
       why: UUUU pricing hinges on spot uranium
       focus: Cameco Q3, Sprott Physical flows
    5. synthesize_memo — crystallize

We extract the ordered list of task types. dive_custom carries additional
`specialty`, `why`, `focus` fields parsed from the indented lines below.
"""

from __future__ import annotations

import re

from praxis_core.schemas.task_types import TaskType

VALID_DIVE_TASK_TYPES: tuple[TaskType, ...] = (
    TaskType.DIVE_FINANCIAL_RIGOROUS,
    TaskType.DIVE_BUSINESS_MOAT,
    TaskType.DIVE_INDUSTRY_STRUCTURE,
    TaskType.DIVE_CAPITAL_ALLOCATION,
    TaskType.DIVE_GEOPOLITICAL_RISK,
    TaskType.DIVE_MACRO,
    TaskType.DIVE_CUSTOM,
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
