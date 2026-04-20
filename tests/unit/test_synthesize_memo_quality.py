"""Unit tests for D27 synthesize_memo quality gates."""

from __future__ import annotations

from pathlib import Path

from handlers.synthesize_memo import (
    DiveCoverage,
    _build_coverage_block,
    _memo_quality_sufficient,
)


def _cov(
    *,
    fin_chars: int = 1500,
    verdict: str = "CONTINUE",
    reason: str = "",
    override: bool = False,
    override_decision: str = "NONE",
    present: list[str] | None = None,
) -> DiveCoverage:
    return DiveCoverage(
        financial_path=Path("/nonexistent/financial-rigorous.md"),
        financial_chars=fin_chars,
        financial_investability=verdict,
        financial_stop_reason=reason,
        override_applied=override,
        override_decision=override_decision,
        present=present or ["financial-rigorous", "business-moat"],
    )


# -- quality gate ----


def test_quality_ok_two_specialists_continue():
    ok, _ = _memo_quality_sufficient(_cov())
    assert ok is True


def test_quality_fails_only_one_specialist():
    ok, reason = _memo_quality_sufficient(_cov(present=["financial-rigorous"]))
    assert ok is False
    assert "specialists" in reason


def test_quality_fails_financial_missing():
    ok, reason = _memo_quality_sufficient(_cov(present=["business-moat", "macro"]))
    assert ok is False
    assert "financial-rigorous" in reason


def test_quality_fails_financial_too_short():
    ok, reason = _memo_quality_sufficient(_cov(fin_chars=800))
    assert ok is False
    assert "too short" in reason


def test_quality_fails_stop_not_overridden():
    ok, reason = _memo_quality_sufficient(_cov(verdict="STOP", reason="going concern"))
    assert ok is False
    assert "STOP" in reason


def test_quality_ok_stop_overridden_continue():
    ok, _ = _memo_quality_sufficient(
        _cov(verdict="STOP", reason="r", override=True, override_decision="CONTINUE")
    )
    assert ok is True


def test_quality_fails_stop_overridden_stop():
    # Human override confirmed STOP — the investigation should NOT be resolved
    ok, _ = _memo_quality_sufficient(
        _cov(verdict="STOP", override=True, override_decision="STOP")
    )
    assert ok is False


def test_quality_malformed_investability_treated_as_continue():
    # Fail-open: MALFORMED behaves like CONTINUE for the gate
    ok, _ = _memo_quality_sufficient(_cov(verdict="MALFORMED"))
    assert ok is True


# -- coverage-block builder ----


def test_coverage_block_stop_injects_too_hard():
    block = _build_coverage_block(
        _cov(verdict="STOP", reason="restatement in progress")
    )
    assert "STOP verdict" in block
    assert "Too Hard" in block
    assert "restatement in progress" in block


def test_coverage_block_continue_no_too_hard():
    block = _build_coverage_block(_cov(verdict="CONTINUE"))
    assert "Too Hard" not in block
    assert "STOP verdict" not in block


def test_coverage_block_override_note():
    block = _build_coverage_block(
        _cov(verdict="STOP", reason="r", override=True, override_decision="CONTINUE")
    )
    # Override applied, CONTINUE decision → normal memo; STOP block omitted
    assert "STOP verdict" not in block
    assert "override applied" in block
