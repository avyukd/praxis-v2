from __future__ import annotations

import json
from pathlib import Path

from praxis_core.tasks.validators import (
    validate_compile_to_wiki,
    validate_generate_daily_journal,
    validate_refresh_index,
    validate_triage_filing,
)
from praxis_core.vault import conventions as vc


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


TRIAGE_PAYLOAD = {
    "accession": "0001045810-26-000047",
    "form_type": "8-K",
    "ticker": "NVDA",
    "cik": "0001045810",
    "filing_url": "https://www.sec.gov/...",
    "raw_path": "_raw/filings/8-k/0001045810-26-000047/filing.txt",
}


def test_triage_missing_both_artifacts(tmp_path: Path) -> None:
    r = validate_triage_filing(TRIAGE_PAYLOAD, tmp_path)
    assert not r.is_success
    assert len(r.missing) == 2


def test_triage_partial_md_only(tmp_path: Path) -> None:
    d = vc.analyzed_filing_dir(tmp_path, "8-K", TRIAGE_PAYLOAD["accession"])
    _write(d / "triage.md", "body")
    r = validate_triage_filing(TRIAGE_PAYLOAD, tmp_path)
    assert len(r.ok) == 1
    assert len(r.missing) == 1


def test_triage_full_success(tmp_path: Path) -> None:
    d = vc.analyzed_filing_dir(tmp_path, "8-K", TRIAGE_PAYLOAD["accession"])
    _write(d / "triage.md", "body")
    _write(
        d / "triage.json",
        json.dumps(
            {
                "accession": TRIAGE_PAYLOAD["accession"],
                "form_type": "8-K",
                "ticker": "NVDA",
                "score": 4,
                "category": "guidance",
                "one_sentence_why": "raised",
                "warrants_deep_read": True,
            }
        ),
    )
    r = validate_triage_filing(TRIAGE_PAYLOAD, tmp_path)
    assert r.is_success
    assert len(r.ok) == 2


def test_triage_malformed_json(tmp_path: Path) -> None:
    d = vc.analyzed_filing_dir(tmp_path, "8-K", TRIAGE_PAYLOAD["accession"])
    _write(d / "triage.md", "body")
    _write(d / "triage.json", "{not json")
    r = validate_triage_filing(TRIAGE_PAYLOAD, tmp_path)
    assert r.is_partial
    assert r.malformed[0].path.endswith("triage.json")


def test_triage_json_schema_fail(tmp_path: Path) -> None:
    d = vc.analyzed_filing_dir(tmp_path, "8-K", TRIAGE_PAYLOAD["accession"])
    _write(d / "triage.md", "body")
    _write(
        d / "triage.json",
        json.dumps(
            {
                "accession": "x",
                "form_type": "8-K",
                "ticker": "NVDA",
                "score": 99,  # invalid
                "category": "guidance",
                "one_sentence_why": "x",
                "warrants_deep_read": True,
            }
        ),
    )
    r = validate_triage_filing(TRIAGE_PAYLOAD, tmp_path)
    assert r.is_partial
    assert "score" in r.malformed[0].reason or "ValidationError" in r.malformed[0].reason


def test_refresh_index_missing(tmp_path: Path) -> None:
    r = validate_refresh_index({"scope": "full", "triggered_by": "scheduler"}, tmp_path)
    assert not r.is_success


def test_refresh_index_ok(tmp_path: Path) -> None:
    _write(vc.index_path(tmp_path), "# INDEX")
    r = validate_refresh_index({"scope": "full", "triggered_by": "scheduler"}, tmp_path)
    assert r.is_success


def test_compile_to_wiki_needs_3_files(tmp_path: Path) -> None:
    payload = {
        "source_kind": "filing_analysis",
        "analysis_path": "x",
        "ticker": "NVDA",
        "accession": "0001045810-26-000047",
    }
    _write(vc.index_path(tmp_path), "x")
    _write(vc.log_path(tmp_path), "x")
    r = validate_compile_to_wiki(payload, tmp_path)
    # index + log = 2 files, need 3+
    assert r.is_partial or not r.is_success


def test_compile_to_wiki_tiny_notes_fails(tmp_path: Path) -> None:
    """Short 'x' content in notes.md must not pass validation."""
    payload = {
        "source_kind": "filing_analysis",
        "analysis_path": "_analyzed/filings/8-k/acc-1/analysis.md",
        "ticker": "NVDA",
        "accession": "acc-1",
    }
    _write(vc.index_path(tmp_path), "x")
    _write(vc.log_path(tmp_path), "x")
    _write(vc.company_notes_path(tmp_path, "NVDA"), "x")
    _write(vc.company_journal_path(tmp_path, "NVDA"), "- 2026-04-18 entry")
    r = validate_compile_to_wiki(payload, tmp_path)
    assert r.is_partial
    # Malformed because of tiny notes
    assert any("too small" in m.reason for m in r.malformed)


def test_compile_to_wiki_missing_backlink_fails(tmp_path: Path) -> None:
    """notes.md with enough content but no backlink to analysis_path must fail."""
    payload = {
        "source_kind": "filing_analysis",
        "analysis_path": "_analyzed/filings/8-k/acc-2/analysis.md",
        "ticker": "NVDA",
        "accession": "acc-2",
    }
    _write(vc.index_path(tmp_path), "# INDEX")
    _write(vc.log_path(tmp_path), "- entry")
    _write(
        vc.company_notes_path(tmp_path, "NVDA"),
        "# NVDA\n\n" + ("filler content " * 20),  # >100 chars but no backlink
    )
    _write(vc.company_journal_path(tmp_path, "NVDA"), "- 2026-04-18 compiled")
    r = validate_compile_to_wiki(payload, tmp_path)
    assert r.is_partial
    assert any("missing wikilink" in m.reason for m in r.malformed)


def test_compile_to_wiki_empty_journal_fails(tmp_path: Path) -> None:
    payload = {
        "source_kind": "filing_analysis",
        "analysis_path": "_analyzed/filings/8-k/acc-3/analysis.md",
        "ticker": "NVDA",
        "accession": "acc-3",
    }
    _write(vc.index_path(tmp_path), "# INDEX")
    _write(vc.log_path(tmp_path), "- entry")
    notes_content = f"# NVDA\n\n## 2026-04-18\nSee [[{payload['analysis_path']}]] for analysis.\n"
    _write(vc.company_notes_path(tmp_path, "NVDA"), notes_content + ("x" * 100))
    _write(vc.company_journal_path(tmp_path, "NVDA"), "")  # empty journal
    r = validate_compile_to_wiki(payload, tmp_path)
    assert r.is_partial or not r.is_success
    assert any("empty" in m.reason.lower() for m in r.malformed)


def test_compile_to_wiki_full_success(tmp_path: Path) -> None:
    payload = {
        "source_kind": "filing_analysis",
        "analysis_path": "_analyzed/filings/8-k/acc-4/analysis.md",
        "ticker": "NVDA",
        "accession": "acc-4",
    }
    _write(vc.index_path(tmp_path), "# INDEX\n\n- NVDA")
    _write(vc.log_path(tmp_path), "- 2026-04-18 compile NVDA")
    notes_content = (
        f"# NVDA\n\n## 2026-04-18 compile\n"
        f"See [[{payload['analysis_path']}]] for full analysis.\n" + ("Detail line. " * 15)
    )
    _write(vc.company_notes_path(tmp_path, "NVDA"), notes_content)
    _write(
        vc.company_journal_path(tmp_path, "NVDA"),
        "- 2026-04-18T10:00:00Z: compiled filing_analysis acc-4",
    )
    r = validate_compile_to_wiki(payload, tmp_path)
    assert r.is_success


def test_generate_daily_journal(tmp_path: Path) -> None:
    payload = {"date": "2026-04-18", "triggered_by": "scheduler"}
    r = validate_generate_daily_journal(payload, tmp_path)
    assert not r.is_success
    _write(tmp_path / "journal" / "2026-04-18.md", "x")
    r = validate_generate_daily_journal(payload, tmp_path)
    assert r.is_success


# -- Section A analyze_filing validator tests (two-stage Haiku→Sonnet) --

from praxis_core.tasks.validators import validate_analyze_filing


ANALYZE_PAYLOAD_8K = {
    "accession": "0001045810-26-000047",
    "form_type": "8-K",
    "ticker": "NVDA",
    "cik": "0001045810",
    "raw_path": "_raw/filings/8-k/0001045810-26-000047/filing.txt",
    "source": "edgar",
}


ANALYZE_PAYLOAD_PR = {
    "accession": "gnw-1234",
    "form_type": "press_release",
    "ticker": "NVDA",
    "raw_path": "_raw/press_releases/gnw/NVDA/gnw-1234/release.txt",
    "source": "gnw",
    "release_id": "gnw-1234",
}


def _write_screen(d: Path, outcome: str) -> None:
    _write(
        d / "screen.json",
        json.dumps(
            {
                "accession": "x",
                "outcome": outcome,
                "screened_at": "2026-04-20T09:15:00-04:00",
                "raw_response": outcome,
            }
        ),
    )


def _write_analysis(d: Path) -> None:
    _write(
        d / "analysis.json",
        json.dumps(
            {
                "accession": "x",
                "ticker": "NVDA",
                "form_type": "8-K",
                "source": "edgar",
                "classification": "positive",
                "magnitude": 0.7,
                "new_information": "x",
                "materiality": "x",
                "explanation": "x",
                "analyzed_at": "2026-04-20T09:15:00-04:00",
                "model": "sonnet",
            }
        ),
    )


def test_analyze_missing_screen(tmp_path: Path) -> None:
    r = validate_analyze_filing(ANALYZE_PAYLOAD_8K, tmp_path)
    assert not r.is_success
    assert any("screen.json" in m for m in r.missing)


def test_analyze_negative_screen_is_success(tmp_path: Path) -> None:
    d = vc.analyzed_filing_dir(tmp_path, "8-K", ANALYZE_PAYLOAD_8K["accession"])
    _write_screen(d, "negative")
    r = validate_analyze_filing(ANALYZE_PAYLOAD_8K, tmp_path)
    assert r.is_success, f"negative screen should be a success, got {r}"


def test_analyze_positive_screen_needs_analysis(tmp_path: Path) -> None:
    d = vc.analyzed_filing_dir(tmp_path, "8-K", ANALYZE_PAYLOAD_8K["accession"])
    _write_screen(d, "positive")
    r = validate_analyze_filing(ANALYZE_PAYLOAD_8K, tmp_path)
    assert not r.is_success
    assert any("analysis.json" in m for m in r.missing)


def test_analyze_positive_with_analysis_is_success(tmp_path: Path) -> None:
    d = vc.analyzed_filing_dir(tmp_path, "8-K", ANALYZE_PAYLOAD_8K["accession"])
    _write_screen(d, "positive")
    _write_analysis(d)
    r = validate_analyze_filing(ANALYZE_PAYLOAD_8K, tmp_path)
    assert r.is_success


def test_analyze_malformed_analysis_is_partial(tmp_path: Path) -> None:
    d = vc.analyzed_filing_dir(tmp_path, "8-K", ANALYZE_PAYLOAD_8K["accession"])
    _write_screen(d, "positive")
    _write(d / "analysis.json", "{not json")
    r = validate_analyze_filing(ANALYZE_PAYLOAD_8K, tmp_path)
    assert r.is_partial
    assert any("analysis.json" in m.path for m in r.malformed)


def test_analyze_pr_variant_uses_pr_dir(tmp_path: Path) -> None:
    d = vc.analyzed_pr_dir(tmp_path, "gnw", "NVDA", "gnw-1234")
    _write_screen(d, "positive")
    _write_analysis(d)
    r = validate_analyze_filing(ANALYZE_PAYLOAD_PR, tmp_path)
    assert r.is_success


def test_analyze_pr_missing_ticker_malformed(tmp_path: Path) -> None:
    payload = dict(ANALYZE_PAYLOAD_PR)
    payload["ticker"] = None
    r = validate_analyze_filing(payload, tmp_path)
    assert r.malformed
