"""Idea-surfacing schemas (Section D D44)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SurfacedIdea(BaseModel):
    handle: str
    dedup_handle: str
    idea_type: Literal[
        "theme_intersection",
        "cross_ticker_pattern",
        "thesis_revision",
        "question_answered",
        "concept_promotion",
        "anomaly",
    ]
    tickers: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    summary: str
    rationale: str
    evidence: list[str] = Field(default_factory=list)
    urgency: Literal["low", "medium", "high"]
    surfaced_at: str


class SurfacedIdeaBatch(BaseModel):
    batch_handle: str
    generated_at: str
    ideas: list[SurfacedIdea] = Field(default_factory=list)
    inputs_summary: dict[str, int] = Field(default_factory=dict)
