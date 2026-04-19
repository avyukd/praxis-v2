from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TriageResult(BaseModel):
    accession: str
    form_type: str
    ticker: str | None = None
    score: int = Field(ge=1, le=5)
    category: Literal[
        "earnings",
        "guidance",
        "material_agreement",
        "departure",
        "acquisition",
        "regulatory",
        "other",
        "noise",
    ]
    one_sentence_why: str
    warrants_deep_read: bool


class AnalysisThesisImpact(BaseModel):
    handle: str
    direction: Literal["supportive", "refutes", "neutral"]
    confidence: float = Field(ge=0.0, le=1.0)


class AnalysisSignals(BaseModel):
    accession: str
    ticker: str | None = None
    event_type: str
    trade_relevant: bool
    urgency: Literal["low", "medium", "high", "intraday"]
    specific_claims: list[str] = Field(default_factory=list)
    linked_themes: list[str] = Field(default_factory=list)
    linked_concepts: list[str] = Field(default_factory=list)
    thesis_impacts: list[AnalysisThesisImpact] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str


class LintFinding(BaseModel):
    severity: Literal["error", "warning", "info"]
    kind: Literal[
        "broken_wikilink",
        "orphan_note",
        "missing_frontmatter",
        "stale_active_note",
        "contradiction",
        "missing_concept_promotion",
    ]
    path: str
    description: str


class LintReport(BaseModel):
    ran_at: str
    findings: list[LintFinding] = Field(default_factory=list)
    vault_stats: dict[str, int] = Field(default_factory=dict)


class ValidationMalformed(BaseModel):
    path: str
    reason: str


class ValidationResult(BaseModel):
    ok: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    malformed: list[ValidationMalformed] = Field(default_factory=list)

    @property
    def is_success(self) -> bool:
        return not self.missing and not self.malformed

    @property
    def is_partial(self) -> bool:
        return bool(self.ok) and (bool(self.missing) or bool(self.malformed))
