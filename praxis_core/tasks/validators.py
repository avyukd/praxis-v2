from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from praxis_core.schemas.artifacts import (
    AnalysisSignals,
    TriageResult,
    ValidationMalformed,
    ValidationResult,
)
from praxis_core.schemas.payloads import (
    AnalyzeFilingPayload,
    CompileToWikiPayload,
    DiveBusinessPayload,
    GenerateDailyJournalPayload,
    LintVaultPayload,
    NotifyPayload,
    OrchestrateDivePayload,
    RefreshIndexPayload,
    SynthesizeMemoPayload,
    TriageFilingPayload,
)
from praxis_core.schemas.task_types import TaskType
from praxis_core.vault import conventions as vc

ValidatorFn = Callable[[dict[str, Any], Path], ValidationResult]


def _check_file_exists(path: Path) -> tuple[str, bool]:
    return (str(path), path.exists() and path.is_file())


def _check_pydantic_file(
    path: Path, model: type, *, json_file: bool = True
) -> tuple[str, str | None]:
    """Returns (str_path, None) if ok, (str_path, error_msg) if malformed."""
    if not path.exists():
        return (str(path), "missing")
    try:
        if json_file:
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = path.read_text(encoding="utf-8")
        model.model_validate(data)
    except Exception as e:
        return (str(path), f"{type(e).__name__}: {e}")
    return (str(path), None)


def validate_triage_filing(payload_raw: dict[str, Any], vault_root: Path) -> ValidationResult:
    payload = TriageFilingPayload.model_validate(payload_raw)
    d = vc.analyzed_filing_dir(vault_root, payload.form_type, payload.accession)
    triage_md = d / "triage.md"
    triage_json = d / "triage.json"
    ok: list[str] = []
    missing: list[str] = []
    malformed: list[ValidationMalformed] = []
    for p in (triage_md,):
        s, exists = _check_file_exists(p)
        (ok if exists else missing).append(s)
    s, err = _check_pydantic_file(triage_json, TriageResult, json_file=True)
    if err is None:
        ok.append(s)
    elif err == "missing":
        missing.append(s)
    else:
        malformed.append(ValidationMalformed(path=s, reason=err))
    return ValidationResult(ok=ok, missing=missing, malformed=malformed)


def validate_analyze_filing(payload_raw: dict[str, Any], vault_root: Path) -> ValidationResult:
    payload = AnalyzeFilingPayload.model_validate(payload_raw)
    d = vc.analyzed_filing_dir(vault_root, payload.form_type, payload.accession)
    analysis_md = d / "analysis.md"
    signals_json = d / "signals.json"
    ok: list[str] = []
    missing: list[str] = []
    malformed: list[ValidationMalformed] = []
    s, exists = _check_file_exists(analysis_md)
    (ok if exists else missing).append(s)
    s, err = _check_pydantic_file(signals_json, AnalysisSignals, json_file=True)
    if err is None:
        ok.append(s)
    elif err == "missing":
        missing.append(s)
    else:
        malformed.append(ValidationMalformed(path=s, reason=err))
    return ValidationResult(ok=ok, missing=missing, malformed=malformed)


def validate_compile_to_wiki(payload_raw: dict[str, Any], vault_root: Path) -> ValidationResult:
    payload = CompileToWikiPayload.model_validate(payload_raw)
    ok: list[str] = []
    missing: list[str] = []
    malformed: list[ValidationMalformed] = []

    # Always expect INDEX and LOG touched
    for p in (vc.index_path(vault_root), vc.log_path(vault_root)):
        s, exists = _check_file_exists(p)
        (ok if exists else missing).append(s)

    if payload.ticker:
        notes = vc.company_notes_path(vault_root, payload.ticker)
        journal = vc.company_journal_path(vault_root, payload.ticker)
        for p in (notes, journal):
            s, exists = _check_file_exists(p)
            (ok if exists else missing).append(s)

    touched = len(ok)
    if touched < 3:
        malformed.append(
            ValidationMalformed(
                path="<compile>", reason=f"compile touched only {touched} files; need ≥3"
            )
        )
    return ValidationResult(ok=ok, missing=missing, malformed=malformed)


def validate_notify(payload_raw: dict[str, Any], vault_root: Path) -> ValidationResult:
    NotifyPayload.model_validate(payload_raw)
    return ValidationResult(ok=["notify.ntfy_push"], missing=[], malformed=[])


def validate_orchestrate_dive(payload_raw: dict[str, Any], vault_root: Path) -> ValidationResult:
    payload = OrchestrateDivePayload.model_validate(payload_raw)
    p = vc.investigation_path(vault_root, payload.investigation_handle)
    s, exists = _check_file_exists(p)
    if exists:
        return ValidationResult(ok=[s])
    return ValidationResult(missing=[s])


def _validate_dive_generic(
    payload_raw: dict[str, Any], vault_root: Path, section: str
) -> ValidationResult:
    payload = DiveBusinessPayload.model_validate(payload_raw)  # same shape for all dive_*
    notes = vc.company_notes_path(vault_root, payload.ticker)
    s, exists = _check_file_exists(notes)
    ok, missing = ([], [])
    if exists:
        ok.append(s)
        content = notes.read_text(encoding="utf-8")
        if section not in content:
            return ValidationResult(
                ok=ok,
                malformed=[ValidationMalformed(path=s, reason=f"section '{section}' not found")],
            )
    else:
        missing.append(s)
    journal = vc.company_journal_path(vault_root, payload.ticker)
    s, exists = _check_file_exists(journal)
    (ok if exists else missing).append(s)
    return ValidationResult(ok=ok, missing=missing)


def validate_dive_business(payload_raw: dict[str, Any], vault_root: Path) -> ValidationResult:
    return _validate_dive_generic(payload_raw, vault_root, "## Business")


def validate_dive_moat(payload_raw: dict[str, Any], vault_root: Path) -> ValidationResult:
    return _validate_dive_generic(payload_raw, vault_root, "## Moat")


def validate_dive_financials(payload_raw: dict[str, Any], vault_root: Path) -> ValidationResult:
    payload_for_generic = {
        "ticker": payload_raw.get("ticker"),
        "investigation_handle": payload_raw.get("investigation_handle"),
    }
    result = _validate_dive_generic(payload_for_generic, vault_root, "## Financials")
    return result


def validate_synthesize_memo(payload_raw: dict[str, Any], vault_root: Path) -> ValidationResult:
    payload = SynthesizeMemoPayload.model_validate(payload_raw)
    memo = vc.company_memo_path(vault_root, payload.ticker, payload.memo_handle)
    s, exists = _check_file_exists(memo)
    if exists:
        return ValidationResult(ok=[s])
    return ValidationResult(missing=[s])


def validate_refresh_index(payload_raw: dict[str, Any], vault_root: Path) -> ValidationResult:
    RefreshIndexPayload.model_validate(payload_raw)
    s, exists = _check_file_exists(vc.index_path(vault_root))
    if exists:
        return ValidationResult(ok=[s])
    return ValidationResult(missing=[s])


def validate_lint_vault(payload_raw: dict[str, Any], vault_root: Path) -> ValidationResult:
    from praxis_core.time_et import et_date_str

    LintVaultPayload.model_validate(payload_raw)
    p = vault_root / "journal" / f"{et_date_str()}-lint.md"
    s, exists = _check_file_exists(p)
    if exists:
        return ValidationResult(ok=[s])
    return ValidationResult(missing=[s])


def validate_generate_daily_journal(
    payload_raw: dict[str, Any], vault_root: Path
) -> ValidationResult:
    payload = GenerateDailyJournalPayload.model_validate(payload_raw)
    p = vault_root / "journal" / f"{payload.date}.md"
    s, exists = _check_file_exists(p)
    if exists:
        return ValidationResult(ok=[s])
    return ValidationResult(missing=[s])


def validate_rate_limit_probe(payload_raw: dict[str, Any], vault_root: Path) -> ValidationResult:
    # No on-disk artifact required; the worker uses handler's ok/result to drive state machine.
    return ValidationResult(ok=["probe.completed"])


def validate_cleanup_sessions(payload_raw: dict[str, Any], vault_root: Path) -> ValidationResult:
    # No on-disk artifact required.
    return ValidationResult(ok=["cleanup.completed"])


VALIDATORS: dict[str, ValidatorFn] = {
    TaskType.TRIAGE_FILING.value: validate_triage_filing,
    TaskType.ANALYZE_FILING.value: validate_analyze_filing,
    TaskType.COMPILE_TO_WIKI.value: validate_compile_to_wiki,
    TaskType.NOTIFY.value: validate_notify,
    TaskType.ORCHESTRATE_DIVE.value: validate_orchestrate_dive,
    TaskType.DIVE_BUSINESS.value: validate_dive_business,
    TaskType.DIVE_MOAT.value: validate_dive_moat,
    TaskType.DIVE_FINANCIALS.value: validate_dive_financials,
    TaskType.SYNTHESIZE_MEMO.value: validate_synthesize_memo,
    TaskType.REFRESH_INDEX.value: validate_refresh_index,
    TaskType.LINT_VAULT.value: validate_lint_vault,
    TaskType.GENERATE_DAILY_JOURNAL.value: validate_generate_daily_journal,
    TaskType.RATE_LIMIT_PROBE.value: validate_rate_limit_probe,
    TaskType.CLEANUP_SESSIONS.value: validate_cleanup_sessions,
}


def get_validator(task_type: str) -> ValidatorFn | None:
    return VALIDATORS.get(task_type)
