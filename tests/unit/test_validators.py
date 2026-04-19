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


def test_compile_to_wiki_full(tmp_path: Path) -> None:
    payload = {
        "source_kind": "filing_analysis",
        "analysis_path": "x",
        "ticker": "NVDA",
        "accession": "0001045810-26-000047",
    }
    _write(vc.index_path(tmp_path), "x")
    _write(vc.log_path(tmp_path), "x")
    _write(vc.company_notes_path(tmp_path, "NVDA"), "x")
    _write(vc.company_journal_path(tmp_path, "NVDA"), "x")
    r = validate_compile_to_wiki(payload, tmp_path)
    assert r.is_success


def test_generate_daily_journal(tmp_path: Path) -> None:
    payload = {"date": "2026-04-18", "triggered_by": "scheduler"}
    r = validate_generate_daily_journal(payload, tmp_path)
    assert not r.is_success
    _write(tmp_path / "journal" / "2026-04-18.md", "x")
    r = validate_generate_daily_journal(payload, tmp_path)
    assert r.is_success
