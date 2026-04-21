from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from praxis_core.time_et import now_et

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]*$")
_SLUG_NON_ALNUM = re.compile(r"[^a-zA-Z0-9_\-]+")


def _validate_ticker(ticker: str) -> str:
    if not ticker:
        raise ValueError("ticker must be non-empty")
    upper = ticker.upper()
    if not _TICKER_RE.match(upper):
        raise ValueError(f"invalid ticker: {ticker!r}")
    return upper


def _slug(text: str) -> str:
    slug = _SLUG_NON_ALNUM.sub("-", text.lower()).strip("-")
    return slug or "unnamed"


def _date_str(dt: datetime | None) -> str:
    when = dt if dt is not None else now_et()
    return when.strftime("%Y-%m-%d")


def index_path(vault: Path) -> Path:
    return Path(vault) / "INDEX.md"


def log_path(vault: Path) -> Path:
    return Path(vault) / "LOG.md"


def schema_path(vault: Path) -> Path:
    return Path(vault) / "CLAUDE.md"


def company_dir(vault: Path, ticker: str) -> Path:
    return Path(vault) / "companies" / _validate_ticker(ticker)


def company_notes_path(vault: Path, ticker: str) -> Path:
    return company_dir(vault, ticker) / "notes.md"


def company_thesis_path(vault: Path, ticker: str) -> Path:
    return company_dir(vault, ticker) / "thesis.md"


def company_journal_path(vault: Path, ticker: str) -> Path:
    return company_dir(vault, ticker) / "journal.md"


def company_memo_path(
    vault: Path,
    ticker: str,
    handle_or_title: str,
    dt: datetime | None = None,
) -> Path:
    return company_dir(vault, ticker) / "memos" / f"{_date_str(dt)}-{_slug(handle_or_title)}.md"


def crosscut_memo_path(vault: Path, title: str, dt: datetime | None = None) -> Path:
    return Path(vault) / "memos" / f"{_date_str(dt)}-{_slug(title)}.md"


def theme_path(vault: Path, name: str) -> Path:
    return Path(vault) / "themes" / f"{_slug(name)}.md"


def investigation_path(vault: Path, handle: str) -> Path:
    return Path(vault) / "investigations" / f"{_slug(handle)}.md"


def journal_daily_path(vault: Path, dt: datetime | None = None) -> Path:
    return Path(vault) / "journal" / f"{_date_str(dt)}.md"


def raw_filing_dir(vault: Path, form_type: str, accession: str) -> Path:
    return Path(vault) / "_raw" / "filings" / form_type.lower() / accession


def analyzed_filing_dir(vault: Path, form_type: str, accession: str) -> Path:
    return Path(vault) / "_analyzed" / "filings" / form_type.lower() / accession


def raw_manual_path(vault: Path, dt: datetime, slug: str) -> Path:
    return Path(vault) / "_raw" / "manual" / _date_str(dt) / f"{slug}.md"


def inbox_manual_path(vault: Path, dt: datetime, slug: str) -> Path:
    return Path(vault) / "_inbox_manual" / _date_str(dt) / f"{slug}.md"


def raw_pr_dir(vault: Path, source: str, ticker: str, release_id: str) -> Path:
    return Path(vault) / "_raw" / "press_releases" / source / ticker / release_id


def analyzed_pr_dir(vault: Path, source: str, ticker: str, release_id: str) -> Path:
    return Path(vault) / "_analyzed" / "press_releases" / source / ticker / release_id
