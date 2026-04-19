from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class TriageFilingPayload(BaseModel):
    accession: str
    form_type: Literal["8-K", "10-Q", "10-K"]
    ticker: str | None = None
    cik: str
    filing_url: str
    raw_path: str


class AnalyzeFilingPayload(BaseModel):
    accession: str
    form_type: Literal["8-K", "10-Q", "10-K"]
    ticker: str | None = None
    cik: str
    triage_result_path: str
    raw_path: str


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


class DiveBusinessPayload(BaseModel):
    ticker: str
    investigation_handle: str


class DiveMoatPayload(BaseModel):
    ticker: str
    investigation_handle: str


class DiveFinancialsPayload(BaseModel):
    ticker: str
    investigation_handle: str


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


PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "triage_filing": TriageFilingPayload,
    "analyze_filing": AnalyzeFilingPayload,
    "compile_to_wiki": CompileToWikiPayload,
    "notify": NotifyPayload,
    "orchestrate_dive": OrchestrateDivePayload,
    "dive_business": DiveBusinessPayload,
    "dive_moat": DiveMoatPayload,
    "dive_financials": DiveFinancialsPayload,
    "synthesize_memo": SynthesizeMemoPayload,
    "refresh_index": RefreshIndexPayload,
    "lint_vault": LintVaultPayload,
    "generate_daily_journal": GenerateDailyJournalPayload,
    "rate_limit_probe": RateLimitProbePayload,
    "cleanup_sessions": CleanupSessionsPayload,
}


def validate_payload(task_type: str, payload: dict) -> BaseModel:
    model = PAYLOAD_MODELS.get(task_type)
    if model is None:
        raise ValueError(f"unknown task type: {task_type}")
    return model.model_validate(payload)
