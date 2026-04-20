from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


_VALID_FORM_TYPES = frozenset({"8-K", "10-Q", "10-K", "press_release"})


def _validate_form_type(v: str) -> str:
    if v not in _VALID_FORM_TYPES:
        raise ValueError(
            f"form_type {v!r} not in {sorted(_VALID_FORM_TYPES)}"
        )
    return v


class TriageFilingPayload(BaseModel):
    accession: str
    form_type: str
    ticker: str | None = None
    cik: str
    filing_url: str
    raw_path: str

    @classmethod
    def model_validate(cls, obj, **kwargs):  # type: ignore[override]
        result = super().model_validate(obj, **kwargs)
        _validate_form_type(result.form_type)
        return result


class AnalyzeFilingPayload(BaseModel):
    accession: str
    form_type: str
    ticker: str | None = None
    cik: str | None = None
    triage_result_path: str | None = None
    raw_path: str
    source: str = "edgar"
    release_id: str | None = None

    @classmethod
    def model_validate(cls, obj, **kwargs):  # type: ignore[override]
        result = super().model_validate(obj, **kwargs)
        _validate_form_type(result.form_type)
        return result


class CompileToWikiPayload(BaseModel):
    source_kind: Literal["filing_analysis", "manual_source"]
    analysis_path: str
    ticker: str | None = None
    accession: str | None = None


class NotifyPayload(BaseModel):
    ticker: str | None = None
    signal_type: str
    urgency: Literal["low", "medium", "high", "intraday"]
    title: str
    body: str
    linked_analysis_path: str | None = None


class OrchestrateDivePayload(BaseModel):
    ticker: str
    investigation_handle: str
    thesis_handle: str | None = None
    research_priority: int = 5


class DiveSpecialistPayload(BaseModel):
    """Shared shape for all specialist dives (D19)."""

    ticker: str
    investigation_handle: str
    research_priority: int = 5


class DiveFinancialRigorousPayload(DiveSpecialistPayload):
    pass


class DiveBusinessMoatPayload(DiveSpecialistPayload):
    pass


class DiveIndustryStructurePayload(DiveSpecialistPayload):
    pass


class DiveCapitalAllocationPayload(DiveSpecialistPayload):
    pass


class DiveGeopoliticalRiskPayload(DiveSpecialistPayload):
    pass


class DiveMacroPayload(DiveSpecialistPayload):
    pass


class DiveCustomPayload(DiveSpecialistPayload):
    specialty: str
    why: str = ""
    focus: str = ""


class SynthesizeMemoPayload(BaseModel):
    ticker: str
    investigation_handle: str
    thesis_handle: str | None = None
    memo_handle: str


class RefreshIndexPayload(BaseModel):
    scope: Literal["full", "incremental"] = "incremental"
    triggered_by: str = "scheduler"


class LintVaultPayload(BaseModel):
    triggered_by: str = "scheduler"


class GenerateDailyJournalPayload(BaseModel):
    date: str
    triggered_by: str = "scheduler"


class RateLimitProbePayload(BaseModel):
    triggered_by: str = "dispatcher"


class CleanupSessionsPayload(BaseModel):
    min_age_hours: int = 24
    triggered_by: str = "scheduler"


class SurfaceIdeasPayload(BaseModel):
    triggered_by: str = "scheduler"
    focus: str | None = None


PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "triage_filing": TriageFilingPayload,
    "analyze_filing": AnalyzeFilingPayload,
    "compile_to_wiki": CompileToWikiPayload,
    "notify": NotifyPayload,
    "orchestrate_dive": OrchestrateDivePayload,
    "dive_financial_rigorous": DiveFinancialRigorousPayload,
    "dive_business_moat": DiveBusinessMoatPayload,
    "dive_industry_structure": DiveIndustryStructurePayload,
    "dive_capital_allocation": DiveCapitalAllocationPayload,
    "dive_geopolitical_risk": DiveGeopoliticalRiskPayload,
    "dive_macro": DiveMacroPayload,
    "dive_custom": DiveCustomPayload,
    "synthesize_memo": SynthesizeMemoPayload,
    "refresh_index": RefreshIndexPayload,
    "lint_vault": LintVaultPayload,
    "generate_daily_journal": GenerateDailyJournalPayload,
    "rate_limit_probe": RateLimitProbePayload,
    "cleanup_sessions": CleanupSessionsPayload,
    "surface_ideas": SurfaceIdeasPayload,
}


def validate_payload(task_type: str, payload: dict) -> BaseModel:
    model = PAYLOAD_MODELS.get(task_type)
    if model is None:
        raise ValueError(f"unknown task type: {task_type}")
    return model.model_validate(payload)
