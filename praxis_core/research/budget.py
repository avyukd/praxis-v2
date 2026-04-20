"""ResearchBudget — priority-scaled resource caps per dive (Section B D21).

Ported from praxis-copilot's src/cli/research_prompt.py::ResearchBudget.
Investigations carry a research_priority 0-10; each dive handler derives
its word/lookup caps from the budget corresponding to that priority.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ResearchBudget:
    specialist_words: int
    memo_words: int
    web_lookups: str  # str because "unlimited" is a valid value
    agent_policy: str
    depth_label: str

    @staticmethod
    def from_priority(priority: int) -> ResearchBudget:
        p = max(0, min(10, priority))
        if p <= 2:
            return ResearchBudget(
                specialist_words=500,
                memo_words=1_000,
                web_lookups="3",
                agent_policy="minimal",
                depth_label=f"Quick Screen (priority {p}/10)",
            )
        if p <= 4:
            return ResearchBudget(
                specialist_words=1_000,
                memo_words=1_500,
                web_lookups="7",
                agent_policy="conservative",
                depth_label=f"Standard Scan (priority {p}/10)",
            )
        if p <= 6:
            return ResearchBudget(
                specialist_words=1_500,
                memo_words=2_500,
                web_lookups="10",
                agent_policy="standard",
                depth_label=f"Standard Research (priority {p}/10)",
            )
        if p <= 8:
            return ResearchBudget(
                specialist_words=2_500,
                memo_words=4_000,
                web_lookups="20",
                agent_policy="thorough",
                depth_label=f"Deep Research (priority {p}/10)",
            )
        return ResearchBudget(
            specialist_words=4_000,
            memo_words=6_000,
            web_lookups="unlimited",
            agent_policy="maximum",
            depth_label=f"Full Deep Dive (priority {p}/10)",
        )
