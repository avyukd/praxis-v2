from __future__ import annotations

import pytest
from pydantic import ValidationError

from praxis_core.schemas.artifacts import (
    AnalysisResult,
    ScreenResult,
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


def test_analysis_result_valid() -> None:
    r = AnalysisResult(
        accession="0001045810-26-000047",
        ticker="NVDA",
        form_type="8-K",
        source="edgar",
        classification="positive",
        magnitude=0.7,
        new_information="Raised FY25 revenue guidance by $1B.",
        materiality="Guidance raise equals ~5% of prior consensus.",
        explanation="Positive surprise on guidance; datacenter segment strong.",
        analyzed_at="2026-04-20T09:15:00-04:00",
        model="sonnet",
    )
    assert r.classification == "positive"
    assert 0.0 <= r.magnitude <= 1.0


def test_analysis_result_magnitude_bounds() -> None:
    with pytest.raises(ValidationError):
        AnalysisResult(
            accession="x",
            ticker="NVDA",
            form_type="8-K",
            source="edgar",
            classification="positive",
            magnitude=1.5,
            new_information="x",
            materiality="x",
            explanation="x",
            analyzed_at="2026-04-20T09:15:00-04:00",
            model="sonnet",
        )


def test_analysis_result_classification_enum() -> None:
    with pytest.raises(ValidationError):
        AnalysisResult(
            accession="x",
            ticker="NVDA",
            form_type="8-K",
            source="edgar",
            classification="BUY",  # type: ignore[arg-type]
            magnitude=0.5,
            new_information="x",
            materiality="x",
            explanation="x",
            analyzed_at="2026-04-20T09:15:00-04:00",
            model="sonnet",
        )


def test_screen_result_valid() -> None:
    s = ScreenResult(
        accession="x",
        outcome="neutral",
        screened_at="2026-04-20T09:15:00-04:00",
        raw_response="neutral",
    )
    assert s.outcome == "neutral"


def test_screen_result_outcome_enum() -> None:
    with pytest.raises(ValidationError):
        ScreenResult(
            accession="x",
            outcome="maybe",  # type: ignore[arg-type]
            screened_at="2026-04-20T09:15:00-04:00",
            raw_response="maybe",
        )


def test_validation_result_success() -> None:
    r = ValidationResult(ok=["screen.json", "analysis.json"], missing=[], malformed=[])
    assert r.is_success
    assert not r.is_partial


def test_validation_result_partial() -> None:
    r = ValidationResult(
        ok=["screen.json"],
        missing=["analysis.json"],
        malformed=[],
    )
    assert not r.is_success
    assert r.is_partial


def test_validation_result_malformed_partial() -> None:
    r = ValidationResult(
        ok=["screen.json"],
        malformed=[ValidationMalformed(path="analysis.json", reason="invalid json")],
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
