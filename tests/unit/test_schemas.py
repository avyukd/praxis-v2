from __future__ import annotations

import pytest
from pydantic import ValidationError

from praxis_core.schemas.artifacts import (
    AnalysisSignals,
    TriageResult,
    ValidationMalformed,
    ValidationResult,
)
from praxis_core.schemas.payloads import TriageFilingPayload, validate_payload


def test_triage_result_valid() -> None:
    r = TriageResult(
        accession="0001045810-26-000047",
        form_type="8-K",
        ticker="NVDA",
        score=4,
        category="guidance",
        one_sentence_why="Raised FY guidance, datacenter segment accelerating.",
        warrants_deep_read=True,
    )
    assert r.score == 4


def test_triage_result_score_bounds() -> None:
    with pytest.raises(ValidationError):
        TriageResult(
            accession="x",
            form_type="8-K",
            ticker="NVDA",
            score=6,
            category="guidance",
            one_sentence_why="x",
            warrants_deep_read=True,
        )


def test_analysis_signals_valid() -> None:
    s = AnalysisSignals(
        accession="x",
        ticker="NVDA",
        event_type="earnings_guidance_update",
        trade_relevant=True,
        urgency="intraday",
        specific_claims=["raised FY guidance to X"],
        linked_themes=["ai-capex-digestion"],
        confidence=0.8,
        summary="Guidance raise implies stronger H2.",
    )
    assert s.trade_relevant


def test_validation_result_success() -> None:
    r = ValidationResult(ok=["triage.md", "triage.json"], missing=[], malformed=[])
    assert r.is_success
    assert not r.is_partial


def test_validation_result_partial() -> None:
    r = ValidationResult(
        ok=["triage.md"],
        missing=["triage.json"],
        malformed=[],
    )
    assert not r.is_success
    assert r.is_partial


def test_validation_result_malformed_partial() -> None:
    r = ValidationResult(
        ok=["analysis.md"],
        malformed=[ValidationMalformed(path="signals.json", reason="invalid json")],
    )
    assert r.is_partial


def test_validate_payload_triage_filing() -> None:
    payload = {
        "accession": "0001045810-26-000047",
        "form_type": "8-K",
        "ticker": "NVDA",
        "cik": "0001045810",
        "filing_url": "https://www.sec.gov/...",
        "raw_path": "_raw/filings/8-k/0001045810-26-000047/filing.txt",
    }
    validated = validate_payload("triage_filing", payload)
    assert isinstance(validated, TriageFilingPayload)


def test_validate_payload_unknown_type() -> None:
    with pytest.raises(ValueError, match="unknown task type"):
        validate_payload("nonexistent", {})
