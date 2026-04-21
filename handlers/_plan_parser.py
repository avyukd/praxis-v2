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
from dataclasses import dataclass

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
_LIST_ITEM_RE = re.compile(
    r"^\s*(?:[-*]|\d+\.)\s*(?P<task>dive_\w+|synthesize_memo)\b(?P<rest>.*)$",
    re.IGNORECASE,
)
_FIELD_RE = re.compile(r"^\s*(?P<field>specialty|why|focus)\s*:\s*(?P<value>.+?)\s*$")
_SPECIALTY_INLINE_RE = re.compile(r"\bspecialty\s*=\s*(?P<value>[^—\n]+)")


@dataclass(frozen=True)
class PlannedTask:
    task_type: TaskType
    specialty: str | None = None
    why: str = ""
    focus: str = ""


def parse_plan_entries(markdown: str) -> list[PlannedTask]:
    """Returns ordered, deduplicated planned tasks with custom-dive metadata."""
    if not markdown:
        return []

    plan_match = re.search(
        r"^##\s+Plan\b.*?$(.*?)(?=^##\s|\Z)",
        markdown,
        flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    if not plan_match:
        return []

    plan_body = plan_match.group(1)
    lines = plan_body.splitlines()
    entries: list[PlannedTask] = []
    seen: set[str] = set()
    current_index: int | None = None

    for raw_line in lines:
        line = raw_line.rstrip()
        item_match = _LIST_ITEM_RE.match(line)
        if item_match:
            name = item_match.group("task")
            if name not in _VALID_SET or name in seen:
                current_index = None
                continue
            task_type = TaskType(name)
            rest = item_match.group("rest")
            specialty = None
            if task_type == TaskType.DIVE_CUSTOM:
                inline = _SPECIALTY_INLINE_RE.search(rest)
                if inline:
                    specialty = inline.group("value").strip().strip("`'\"")
            entries.append(PlannedTask(task_type=task_type, specialty=specialty))
            seen.add(name)
            current_index = len(entries) - 1
            continue

        if current_index is None:
            continue
        current = entries[current_index]
        if current.task_type != TaskType.DIVE_CUSTOM:
            continue
        field_match = _FIELD_RE.match(line)
        if field_match is None:
            continue
        field = field_match.group("field").lower()
        value = field_match.group("value").strip()
        entries[current_index] = PlannedTask(
            task_type=current.task_type,
            specialty=value if field == "specialty" else current.specialty,
            why=value if field == "why" else current.why,
            focus=value if field == "focus" else current.focus,
        )

    return entries


def parse_plan(markdown: str) -> list[TaskType]:
    """Returns the ordered list of task types the orchestrator's plan specifies.

    Extracts any known `dive_*` or `synthesize_memo` mention from the `## Plan` section,
    preserving the LLM's ordering. De-duplicates while preserving first occurrence.
    Returns empty list if no plan section found — caller should fall back to default.
    """
    return [entry.task_type for entry in parse_plan_entries(markdown)]
