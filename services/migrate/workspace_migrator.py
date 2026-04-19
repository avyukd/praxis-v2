"""Migrate praxis-copilot/workspace/<TICKER>/ content into v2 vault.

Per-ticker layout in source:
  memo.md                         → companies/<TICKER>/memos/<date>-memo.md
  memo.yaml                       → companies/<TICKER>/data/memo.yaml (preserved as-is)
  rigorous-financial-analyst.md   → companies/<TICKER>/analyst_reports/rigorous-financial.md
  business-moat-analyst.md        → companies/<TICKER>/analyst_reports/business-moat.md
  macro-analyst.md                → companies/<TICKER>/analyst_reports/macro.md
  coordinator_log.md              → companies/<TICKER>/journal.md (append if exists)
  data/fundamentals/*             → companies/<TICKER>/data/fundamentals/*
  macro/*.md                      → collected, dedup'd, promoted to memos/macro/
  .mcp.json / CLAUDE.md / .research-prompt.txt / draft_monitors.yaml  → DROP

Date for memo.md: parse "Date:" line from body, fall back to file mtime.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from praxis_core.logging import get_logger
from praxis_core.time_et import et_iso, to_et
from praxis_core.vault.writer import atomic_write
from services.migrate.frontmatter import serialize
from services.migrate.rename_map import RenameMap
from services.migrate.wikilinks import rewrite_body

log = get_logger("migrate.workspace")


_DATE_PATTERNS = (
    re.compile(r"\*\*Date:\*\*\s*(\d{4}-\d{2}-\d{2})"),
    re.compile(r"^Date:\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE),
    re.compile(r"\*\*Date:\*\*\s*(\d{4}/\d{2}/\d{2})"),
)


def _parse_date_from_body(body: str) -> str | None:
    for pat in _DATE_PATTERNS:
        m = pat.search(body)
        if m:
            s = m.group(1).replace("/", "-")
            try:
                datetime.strptime(s, "%Y-%m-%d")
                return s
            except ValueError:
                continue
    return None


def _extract_ticker_from_memo(body: str) -> str | None:
    # Heading like "# ACHV — Investment Memo"
    m = re.search(r"^#\s+([A-Z][A-Z0-9\.\-]{0,8})\s+[—-]", body, re.MULTILINE)
    if m:
        return m.group(1)
    return None


def _file_mtime_date(p: Path) -> str:
    return to_et(datetime.fromtimestamp(p.stat().st_mtime)).strftime("%Y-%m-%d")


@dataclass
class WorkspaceMigrationReport:
    source_root: Path
    target_root: Path
    tickers_with_memo: int = 0
    tickers_with_analyst_reports: int = 0
    total_files_written: int = 0
    skipped_tickers: list[str] = field(default_factory=list)
    macro_unique: int = 0
    macro_duplicates_dropped: int = 0

    def render(self) -> str:
        lines = [
            "# Workspace migration report",
            "",
            f"Source: {self.source_root}",
            f"Target: {self.target_root}",
            "",
            f"- Tickers with memo.md: {self.tickers_with_memo}",
            f"- Tickers with analyst reports: {self.tickers_with_analyst_reports}",
            f"- Total files written: {self.total_files_written}",
            f"- Unique macro notes (promoted): {self.macro_unique}",
            f"- Duplicate macro notes dropped: {self.macro_duplicates_dropped}",
            f"- Skipped (empty) tickers: {len(self.skipped_tickers)}",
            "",
        ]
        if self.skipped_tickers[:20]:
            lines += ["## First 20 skipped tickers", ""]
            for t in self.skipped_tickers[:20]:
                lines.append(f"- {t}")
            lines.append("")
        return "\n".join(lines)


_ANALYST_REPORTS = {
    "rigorous-financial-analyst.md": "rigorous-financial",
    "business-moat-analyst.md": "business-moat",
    "macro-analyst.md": "macro",
}


def _write_md(
    target: Path,
    body: str,
    metadata: dict[str, Any],
    rename_map: RenameMap | None,
    report: WorkspaceMigrationReport,
) -> None:
    if rename_map is not None:
        rewrite = rewrite_body(body, rename_map)
        body = rewrite.new_body
    final = serialize(metadata, body)
    atomic_write(target, final)
    report.total_files_written += 1


def _migrate_ticker(
    ticker_dir: Path,
    target_root: Path,
    rename_map: RenameMap | None,
    report: WorkspaceMigrationReport,
) -> bool:
    """Returns True if any content was written for this ticker."""
    ticker = ticker_dir.name.upper()
    # Skip special directories like "analyst"
    if ticker == "ANALYST":
        return False

    wrote_any = False

    # memo.md → memos/<date>-memo.md
    memo_md = ticker_dir / "memo.md"
    if memo_md.is_file():
        body = memo_md.read_text(encoding="utf-8", errors="replace")
        date_str = _parse_date_from_body(body) or _file_mtime_date(memo_md)
        heading_ticker = _extract_ticker_from_memo(body)
        effective_ticker = (heading_ticker or ticker).upper()

        target = target_root / f"companies/{effective_ticker}/memos/{date_str}-memo.md"
        metadata: dict[str, Any] = {
            "type": "memo",
            "ticker": effective_ticker,
            "status": "migrated",
            "data_vintage": date_str,
            "source": "copilot_workspace",
            "migrated_from": "copilot_workspace",
            "migrated_at": et_iso(),
        }
        _write_md(target, body, metadata, rename_map, report)
        report.tickers_with_memo += 1
        wrote_any = True

    # memo.yaml → data/memo.yaml (structured companion)
    memo_yaml = ticker_dir / "memo.yaml"
    if memo_yaml.is_file():
        target = target_root / f"companies/{ticker}/data/memo.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(memo_yaml, target)
        report.total_files_written += 1
        wrote_any = True

    # Analyst reports
    has_report = False
    for src_name, report_slug in _ANALYST_REPORTS.items():
        src = ticker_dir / src_name
        if not src.is_file():
            continue
        body = src.read_text(encoding="utf-8", errors="replace")
        target = target_root / f"companies/{ticker}/analyst_reports/{report_slug}.md"
        metadata = {
            "type": "analyst_report",
            "ticker": ticker,
            "specialist": report_slug,
            "status": "migrated",
            "data_vintage": _file_mtime_date(src),
            "source": "copilot_workspace",
            "migrated_from": "copilot_workspace",
            "migrated_at": et_iso(),
        }
        _write_md(target, body, metadata, rename_map, report)
        has_report = True
        wrote_any = True
    if has_report:
        report.tickers_with_analyst_reports += 1

    # coordinator_log.md → journal.md (append or create)
    coord = ticker_dir / "coordinator_log.md"
    if coord.is_file():
        body = coord.read_text(encoding="utf-8", errors="replace")
        target = target_root / f"companies/{ticker}/journal.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            body = existing.rstrip() + "\n\n---\n## coordinator log (migrated)\n\n" + body
        atomic_write(target, body if body.endswith("\n") else body + "\n")
        report.total_files_written += 1
        wrote_any = True

    # data/fundamentals/* → companies/<TICKER>/data/fundamentals/*
    fundamentals_src = ticker_dir / "data" / "fundamentals"
    if fundamentals_src.is_dir():
        target_fund = target_root / f"companies/{ticker}/data/fundamentals"
        target_fund.mkdir(parents=True, exist_ok=True)
        for f in fundamentals_src.iterdir():
            if f.is_file():
                shutil.copy2(f, target_fund / f.name)
                report.total_files_written += 1
                wrote_any = True

    return wrote_any


def _collect_macro_notes(
    workspace_root: Path,
    target_root: Path,
    report: WorkspaceMigrationReport,
) -> None:
    """Dedup macro notes across all ticker workspaces; promote unique ones to memos/macro/."""
    seen_hashes: dict[str, Path] = {}

    for ticker_dir in sorted(workspace_root.iterdir()):
        if not ticker_dir.is_dir():
            continue
        macro_dir = ticker_dir / "macro"
        if not macro_dir.is_dir():
            continue
        for f in macro_dir.iterdir():
            if not f.is_file() or not f.name.endswith(".md"):
                continue
            try:
                content = f.read_bytes()
            except OSError:
                continue
            h = hashlib.sha256(content).hexdigest()[:16]
            if h in seen_hashes:
                report.macro_duplicates_dropped += 1
                continue
            seen_hashes[h] = f

    # Write unique macro notes to target/memos/macro/
    target_dir = target_root / "memos" / "macro"
    target_dir.mkdir(parents=True, exist_ok=True)
    for h, src in sorted(seen_hashes.items()):
        # Preserve filename but slug-safe it
        safe_name = re.sub(r"[^a-zA-Z0-9_\-.]+", "-", src.name)
        target = target_dir / safe_name
        if target.exists():
            target = target_dir / f"{target.stem}-{h[:8]}{target.suffix}"
        body = src.read_text(encoding="utf-8", errors="replace")
        metadata = {
            "type": "memo",
            "memo_kind": "macro_note",
            "status": "migrated",
            "data_vintage": _file_mtime_date(src),
            "source": "copilot_workspace",
            "migrated_from": "copilot_workspace_macro",
            "migrated_at": et_iso(),
            "original_path": src.as_posix(),
        }
        # No wikilink rewriting for macro notes — they pre-date the v1 wiki
        final = serialize(metadata, body)
        atomic_write(target, final)
        report.total_files_written += 1
        report.macro_unique += 1


def migrate_workspace(
    workspace_root: Path,
    target_root: Path,
    *,
    rename_map: RenameMap | None = None,
) -> WorkspaceMigrationReport:
    report = WorkspaceMigrationReport(source_root=workspace_root, target_root=target_root)
    for ticker_dir in sorted(workspace_root.iterdir()):
        if not ticker_dir.is_dir():
            continue
        wrote = _migrate_ticker(ticker_dir, target_root, rename_map, report)
        if not wrote:
            report.skipped_tickers.append(ticker_dir.name)
    _collect_macro_notes(workspace_root, target_root, report)
    return report
