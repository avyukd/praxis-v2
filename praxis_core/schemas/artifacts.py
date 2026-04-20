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


class ScreenResult(BaseModel):
    """Haiku pre-screen output. Persisted at <analyzed_dir>/screen.json."""

    accession: str
    outcome: Literal["positive", "negative", "neutral"]
    screened_at: str
    raw_response: str


class AnalysisResult(BaseModel):
    """Sonnet analysis output. Persisted at <analyzed_dir>/analysis.json.

    Replaces the older AnalysisSignals schema. Flat fields, stock-reaction
    framing (positive/negative/neutral — not investment BUY/SELL).
    """

    accession: str
    ticker: str | None = None
    form_type: str
    source: str
    classification: Literal["positive", "negative", "neutral"]
    magnitude: float = Field(ge=0.0, le=1.0)
    new_information: str
    materiality: str
    explanation: str
    analyzed_at: str
    model: str


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
