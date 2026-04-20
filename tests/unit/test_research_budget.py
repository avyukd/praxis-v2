from __future__ import annotations

from praxis_core.research.budget import ResearchBudget


def test_budget_tiers() -> None:
    b = ResearchBudget.from_priority(0)
    assert b.agent_policy == "minimal"
    assert b.specialist_words == 500

    b = ResearchBudget.from_priority(5)
    assert b.agent_policy == "standard"
    assert b.specialist_words == 1500

    b = ResearchBudget.from_priority(10)
    assert b.agent_policy == "maximum"
    assert b.web_lookups == "unlimited"


def test_budget_clamps() -> None:
    assert ResearchBudget.from_priority(-5).agent_policy == "minimal"
    assert ResearchBudget.from_priority(99).agent_policy == "maximum"
