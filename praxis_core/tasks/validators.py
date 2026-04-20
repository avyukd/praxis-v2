from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from praxis_core.schemas.artifacts import (
    AnalysisResult,
    ScreenResult,
    TriageResult,
    ValidationMalformed,
    ValidationResult,
)
from praxis_core.schemas.payloads import (
    AnalyzeFilingPayload,
    CompileToWikiPayload,
    DiveCustomPayload,
    DiveSpecialistPayload,
    GenerateDailyJournalPayload,
    LintVaultPayload,
    NotifyPayload,
    OrchestrateDivePayload,
    RefreshIndexPayload,
    SurfaceIdeasPayload,
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
    """Two-stage analyze validator (Section A D11).

    Contract:
      - screen.json must exist and parse as ScreenResult (always required)
      - If screen.outcome == "negative": no analysis.json expected. Success.
      - If screen.outcome in {positive, neutral}: analysis.json must exist
        and parse as AnalysisResult. Otherwise partial / missing.
    """
    payload = AnalyzeFilingPayload.model_validate(payload_raw)

    if payload.form_type == "press_release":
        if not payload.ticker or not payload.release_id:
            return ValidationResult(
                malformed=[
                    ValidationMalformed(
                        path="<payload>",
                        reason="press_release analysis requires ticker + release_id",
                    )
                ]
            )
        d = vc.analyzed_pr_dir(vault_root, payload.source, payload.ticker, payload.release_id)
    else:
        d = vc.analyzed_filing_dir(vault_root, payload.form_type, payload.accession)

    screen_path = d / "screen.json"
    analysis_path = d / "analysis.json"

    ok: list[str] = []
    missing: list[str] = []
    malformed: list[ValidationMalformed] = []

    s_screen, err_screen = _check_pydantic_file(screen_path, ScreenResult, json_file=True)
    if err_screen is None:
        ok.append(s_screen)
    elif err_screen == "missing":
        missing.append(s_screen)
        return ValidationResult(ok=ok, missing=missing, malformed=malformed)
    else:
        malformed.append(ValidationMalformed(path=s_screen, reason=err_screen))
        return ValidationResult(ok=ok, missing=missing, malformed=malformed)

    # Read screen outcome to decide whether analysis.json is expected
    try:
        import json as _json

        screen_data = _json.loads(screen_path.read_text(encoding="utf-8"))
        outcome = screen_data.get("outcome")
    except Exception as e:
        malformed.append(ValidationMalformed(path=s_screen, reason=f"read_fail: {e}"))
        return ValidationResult(ok=ok, missing=missing, malformed=malformed)

    if outcome == "negative":
        return ValidationResult(ok=ok, missing=missing, malformed=malformed)

    # positive or neutral — analysis.json required
    s_analysis, err_analysis = _check_pydantic_file(analysis_path, AnalysisResult, json_file=True)
    if err_analysis is None:
        ok.append(s_analysis)
    elif err_analysis == "missing":
        missing.append(s_analysis)
    else:
        malformed.append(ValidationMalformed(path=s_analysis, reason=err_analysis))

    return ValidationResult(ok=ok, missing=missing, malformed=malformed)


_MIN_NOTE_LEN = 100  # "x" alone shouldn't pass validation


def validate_compile_to_wiki(payload_raw: dict[str, Any], vault_root: Path) -> ValidationResult:
    """Validates compile_to_wiki artifacts per Section D D39 + D43 + D38.

    Changes from prior version:
      - No longer requires INDEX.md (D39 decouples INDEX from compile)
      - Strict wikilink regex for analysis backlink (D43): [[<analysis_path>]]
        with literal brackets, not bare substring
      - Shrink-guard: if a pre-write backup exists in _backups/compile/,
        notes.md must not have lost >25% of content (D38)
    """
    import re as _re

    payload = CompileToWikiPayload.model_validate(payload_raw)
    ok: list[str] = []
    missing: list[str] = []
    malformed: list[ValidationMalformed] = []

    s_log, log_exists = _check_file_exists(vc.log_path(vault_root))
    (ok if log_exists else missing).append(s_log)

    if payload.ticker:
        notes = vc.company_notes_path(vault_root, payload.ticker)
        journal = vc.company_journal_path(vault_root, payload.ticker)

        s_notes, notes_exists = _check_file_exists(notes)
        if notes_exists:
            text_body = notes.read_text(encoding="utf-8", errors="replace")
            if len(text_body) < _MIN_NOTE_LEN:
                malformed.append(
                    ValidationMalformed(
                        path=s_notes,
                        reason=f"notes.md too small ({len(text_body)} chars < {_MIN_NOTE_LEN})",
                    )
                )
            elif payload.analysis_path:
                # D43: require wikilink form, not just substring
                wl_pattern = _re.escape(payload.analysis_path)
                wl_match = _re.search(
                    rf"\[\[{wl_pattern}(?:\|[^\]]+)?(?:#[^\]]+)?\]\]", text_body
                )
                if not wl_match:
                    malformed.append(
                        ValidationMalformed(
                            path=s_notes,
                            reason=(
                                f"notes.md missing wikilink [[{payload.analysis_path}]] — "
                                "bare substring refs no longer accepted"
                            ),
                        )
                    )
                else:
                    ok.append(s_notes)
            else:
                ok.append(s_notes)

            # D38 shrink-guard: find latest backup for this ticker, compare sizes
            backups_root = vault_root / "_backups" / "compile"
            if backups_root.exists():
                candidates: list[Path] = []
                for date_dir in backups_root.iterdir():
                    if not date_dir.is_dir():
                        continue
                    for f in date_dir.iterdir():
                        if f.is_file() and payload.ticker.upper() in f.name:
                            candidates.append(f)
                if candidates:
                    latest = max(candidates, key=lambda p: p.stat().st_mtime)
                    try:
                        prior_size = latest.stat().st_size
                        current_size = notes.stat().st_size
                        if prior_size > 0 and current_size < prior_size * 0.75:
                            malformed.append(
                                ValidationMalformed(
                                    path=s_notes,
                                    reason=(
                                        f"notes.md shrunk {prior_size}→{current_size} bytes "
                                        f"(>25% loss; backup at {latest})"
                                    ),
                                )
                            )
                    except OSError:
                        pass
        else:
            missing.append(s_notes)

        s_journal, j_exists = _check_file_exists(journal)
        if j_exists:
            jtext = journal.read_text(encoding="utf-8", errors="replace")
            if len(jtext) < 20:
                malformed.append(
                    ValidationMalformed(path=s_journal, reason="journal.md effectively empty")
                )
            else:
                ok.append(s_journal)
        else:
            missing.append(s_journal)

    touched = len(ok)
    if touched < 2:  # D39 — lowered from 3 since INDEX.md no longer required
        malformed.append(
            ValidationMalformed(
                path="<compile>", reason=f"compile touched only {touched} files; need ≥2"
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


_FUNDAMENTALS_TOOL_RE = re.compile(
    r"mcp__fundamentals__\w+|\[fundamentals:\s*\w+", re.IGNORECASE
)
_WEB_RETRIEVAL_RE = re.compile(
    r"WebFetch\s*\(|WebSearch\s*\(|Bash\(curl|https?://[^\s)\]]+", re.IGNORECASE
)
_WIKILINK_CITATION_RE = re.compile(r"\[\[_raw/[^\]]+\]\]|\[\[_analyzed/[^\]]+\]\]")
_SOURCES_CONSULTED_RE = re.compile(r"^##\s+Sources consulted\b", re.MULTILINE | re.IGNORECASE)


def _check_research_depth(
    path_str: str, content: str
) -> list[ValidationMalformed]:
    """Enforce dive-quality contract: required Sources section + proof of
    primary-source retrieval. Returns list of malformed entries (empty if OK)."""
    issues: list[ValidationMalformed] = []
    if not _SOURCES_CONSULTED_RE.search(content):
        issues.append(
            ValidationMalformed(
                path=path_str,
                reason="missing required '## Sources consulted' section",
            )
        )
    fundamentals_hits = len(set(_FUNDAMENTALS_TOOL_RE.findall(content)))
    web_hits = len(set(_WEB_RETRIEVAL_RE.findall(content)))
    wikilink_hits = len(set(_WIKILINK_CITATION_RE.findall(content)))
    # Require at least 3 retrieval markers from ANY combination of fundamentals
    # MCP, web fetches, or vault wikilinks. Most dives should easily exceed.
    if fundamentals_hits + web_hits + wikilink_hits < 3:
        issues.append(
            ValidationMalformed(
                path=path_str,
                reason=(
                    f"insufficient research evidence: {fundamentals_hits} "
                    f"fundamentals-MCP + {web_hits} web + {wikilink_hits} "
                    "_raw/_analyzed citations (need >=3 total)"
                ),
            )
        )
    return issues


def _check_word_budget(
    path_str: str, content: str, research_priority: int
) -> list[ValidationMalformed]:
    """Enforce the ResearchBudget specialist_words cap for this priority,
    with a 30% overage tolerance (a useful table that pushes the cap is
    fine; a rambling 2x-over dive is not)."""
    from praxis_core.research.budget import ResearchBudget

    budget = ResearchBudget.from_priority(research_priority)
    word_count = len(content.split())
    cap = int(budget.specialist_words * 1.3)
    if word_count > cap:
        return [
            ValidationMalformed(
                path=path_str,
                reason=(
                    f"dive exceeds word budget ({word_count} words > "
                    f"{cap} = 1.3x {budget.depth_label})"
                ),
            )
        ]
    return []


def _validate_specialist_dive(
    payload_raw: dict[str, Any],
    vault_root: Path,
    specialty_slug: str,
    min_chars: int = 500,
) -> ValidationResult:
    """D19/D53 — specialists write companies/<TICKER>/dives/<specialty>.md
    as a standalone file. Dive-quality refactor: also check Sources consulted
    + retrieval evidence + word-budget compliance."""
    payload = DiveSpecialistPayload.model_validate(payload_raw)
    out_path = vc.company_dir(vault_root, payload.ticker) / "dives" / f"{specialty_slug}.md"
    s, exists = _check_file_exists(out_path)
    if not exists:
        return ValidationResult(missing=[s])
    content = out_path.read_text(encoding="utf-8", errors="replace")
    if len(content) < min_chars:
        return ValidationResult(
            malformed=[
                ValidationMalformed(
                    path=s,
                    reason=f"dive output too small ({len(content)} chars < {min_chars})",
                )
            ]
        )
    issues: list[ValidationMalformed] = []
    issues.extend(_check_research_depth(s, content))
    issues.extend(_check_word_budget(s, content, payload.research_priority))
    if issues:
        return ValidationResult(malformed=issues)
    return ValidationResult(ok=[s])


def validate_dive_financial_rigorous(
    payload_raw: dict[str, Any], vault_root: Path
) -> ValidationResult:
    """Financial-rigorous also requires the INVESTABILITY line (D20).
    Additional strictness: missing INVESTABILITY line is now malformed,
    not fail-open — the investability handler runs AFTER validation, so a
    missing line has no gate to fall through to. Better to retry."""
    payload = DiveSpecialistPayload.model_validate(payload_raw)
    out_path = vc.company_dir(vault_root, payload.ticker) / "dives" / "financial-rigorous.md"
    s, exists = _check_file_exists(out_path)
    if not exists:
        return ValidationResult(missing=[s])
    content = out_path.read_text(encoding="utf-8", errors="replace")
    issues: list[ValidationMalformed] = []
    if len(content) < 500:
        issues.append(
            ValidationMalformed(path=s, reason=f"dive output too small ({len(content)} chars)")
        )
    else:
        issues.extend(_check_research_depth(s, content))
        issues.extend(_check_word_budget(s, content, payload.research_priority))
        if not re.search(r"^\s*INVESTABILITY:\s*(CONTINUE|STOP)\s*[—-]", content, re.MULTILINE | re.IGNORECASE):
            issues.append(
                ValidationMalformed(
                    path=s,
                    reason="missing INVESTABILITY: CONTINUE|STOP verdict line",
                )
            )
    if issues:
        return ValidationResult(malformed=issues)
    return ValidationResult(ok=[s])


def validate_dive_business_moat(payload_raw: dict[str, Any], vault_root: Path) -> ValidationResult:
    return _validate_specialist_dive(payload_raw, vault_root, "business-moat")


def validate_dive_industry_structure(
    payload_raw: dict[str, Any], vault_root: Path
) -> ValidationResult:
    return _validate_specialist_dive(payload_raw, vault_root, "industry-structure")


def validate_dive_capital_allocation(
    payload_raw: dict[str, Any], vault_root: Path
) -> ValidationResult:
    return _validate_specialist_dive(payload_raw, vault_root, "capital-allocation")


def validate_dive_geopolitical_risk(
    payload_raw: dict[str, Any], vault_root: Path
) -> ValidationResult:
    return _validate_specialist_dive(payload_raw, vault_root, "geopolitical-risk")


def validate_dive_macro(payload_raw: dict[str, Any], vault_root: Path) -> ValidationResult:
    return _validate_specialist_dive(payload_raw, vault_root, "macro")


def validate_dive_custom(payload_raw: dict[str, Any], vault_root: Path) -> ValidationResult:
    from handlers.dive_custom import _slugify

    payload = DiveCustomPayload.model_validate(payload_raw)
    specialty_slug = _slugify(payload.specialty)
    return _validate_specialist_dive(payload_raw, vault_root, specialty_slug)


def validate_surface_ideas(payload_raw: dict[str, Any], vault_root: Path) -> ValidationResult:
    SurfaceIdeasPayload.model_validate(payload_raw)
    # surface_ideas handler writes _surfaced/<date>/ideas-<HHMM>.md; validator
    # is tolerant — we don't insist the LLM produced ideas (empty batch is valid)
    return ValidationResult(ok=["surface_ideas.completed"])


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
    TaskType.DIVE_FINANCIAL_RIGOROUS.value: validate_dive_financial_rigorous,
    TaskType.DIVE_BUSINESS_MOAT.value: validate_dive_business_moat,
    TaskType.DIVE_INDUSTRY_STRUCTURE.value: validate_dive_industry_structure,
    TaskType.DIVE_CAPITAL_ALLOCATION.value: validate_dive_capital_allocation,
    TaskType.DIVE_GEOPOLITICAL_RISK.value: validate_dive_geopolitical_risk,
    TaskType.DIVE_MACRO.value: validate_dive_macro,
    TaskType.DIVE_CUSTOM.value: validate_dive_custom,
    TaskType.SYNTHESIZE_MEMO.value: validate_synthesize_memo,
    TaskType.REFRESH_INDEX.value: validate_refresh_index,
    TaskType.LINT_VAULT.value: validate_lint_vault,
    TaskType.GENERATE_DAILY_JOURNAL.value: validate_generate_daily_journal,
    TaskType.RATE_LIMIT_PROBE.value: validate_rate_limit_probe,
    TaskType.CLEANUP_SESSIONS.value: validate_cleanup_sessions,
    TaskType.SURFACE_IDEAS.value: validate_surface_ideas,
}


def get_validator(task_type: str) -> ValidatorFn | None:
    return VALIDATORS.get(task_type)
