"""Import praxis-copilot local state YAML into v2 Postgres.

In scope:
  - `data/filing_research_state_YYYY-MM-DD.yaml` — historical filing decisions. Each filing
    becomes a `signals_fired` row with urgency derived from magnitude.
  - `data/analyst_state.yaml` — alert triage reactions. Each reaction becomes a
    `signals_fired` row with signal_type="alert_triage".
  - `data/queue_state.yaml` — research queue (GitHub issue mirror). Emits a migration_report
    with the active items so you can decide which to turn into v2 investigations manually.

Out of scope:
  - HTML reports (regenerable)
  - Telemetry .jsonl (token usage logs, no signal value)
  - IPC dirs (ephemeral)
  - .env / config (secrets)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from praxis_core.db.models import SignalFired
from praxis_core.logging import get_logger
from praxis_core.time_et import ET

log = get_logger("migrate.copilot_state")


def _magnitude_to_urgency(magnitude: Any) -> str:
    try:
        m = float(magnitude)
    except (TypeError, ValueError):
        return "medium"
    if m >= 0.85:
        return "intraday"
    if m >= 0.65:
        return "high"
    if m >= 0.35:
        return "medium"
    return "low"


def _parse_iso(s: Any) -> datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ET)
        return dt
    except ValueError:
        return None


@dataclass
class CopilotStateReport:
    filings_imported: int = 0
    alerts_imported: int = 0
    queue_items_considered: int = 0
    queue_items_ready_for_investigation: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "# Copilot state migration report",
            "",
            f"Filings imported as signals_fired: {self.filings_imported}",
            f"Analyst alerts imported: {self.alerts_imported}",
            f"Queue items considered: {self.queue_items_considered}",
            f"Queue items worth turning into investigations: {len(self.queue_items_ready_for_investigation)}",
            "",
            "## Files read",
        ]
        for f in self.files_read:
            lines.append(f"- {f}")
        if self.queue_items_ready_for_investigation:
            lines += ["", "## Queue items that look worth an investigation"]
            for item in self.queue_items_ready_for_investigation[:50]:
                lines.append(f"- {item}")
        return "\n".join(lines) + "\n"


async def _import_filing_state_file(
    session: AsyncSession,
    path: Path,
    report: CopilotStateReport,
    dry_run: bool,
) -> None:
    try:
        with path.open() as f:
            data = yaml.safe_load(f)
    except Exception as e:
        log.warning("copilot_state.file_read_fail", path=str(path), error=str(e))
        return
    report.files_read.append(path.as_posix())
    if not isinstance(data, dict):
        return
    filings = data.get("filings") or {}
    if not isinstance(filings, dict):
        return

    for key, filing in filings.items():
        if not isinstance(filing, dict):
            continue
        ticker = filing.get("ticker")
        classification = filing.get("classification")
        magnitude = filing.get("magnitude")
        summary = filing.get("summary") or ""
        finished_at = _parse_iso(filing.get("research_finished_at"))
        started_at = _parse_iso(filing.get("research_started_at"))

        payload = {
            "title": f"[migrated] {ticker or '-'} {classification or ''}",
            "body": (summary or "")[:1000],
            "migrated_key": key,
            "magnitude": magnitude,
            "classification": classification,
            "decision": filing.get("decision"),
            "decision_reason": (filing.get("decision_reason") or "")[:500],
        }

        urgency = _magnitude_to_urgency(magnitude)
        fired_at = finished_at or started_at or datetime.now(ET)

        if not dry_run:
            session.add(
                SignalFired(
                    id=uuid.uuid4(),
                    task_id=None,
                    ticker=str(ticker)[:16] if ticker else None,
                    signal_type="historical_filing_analysis",
                    urgency=urgency,
                    payload=payload,
                    fired_at=fired_at,
                )
            )
        report.filings_imported += 1


async def _import_analyst_state(
    session: AsyncSession,
    path: Path,
    report: CopilotStateReport,
    dry_run: bool,
) -> None:
    try:
        with path.open() as f:
            data = yaml.safe_load(f)
    except Exception as e:
        log.warning("copilot_state.analyst_read_fail", error=str(e))
        return
    report.files_read.append(path.as_posix())
    reactions = data.get("reactions") or [] if isinstance(data, dict) else []
    if not isinstance(reactions, list):
        return
    for r in reactions:
        if not isinstance(r, dict):
            continue
        ticker = r.get("ticker")
        urgency = {
            "low": "low",
            "medium": "medium",
            "high": "high",
            "intraday": "intraday",
        }.get(str(r.get("urgency", "medium")).lower(), "medium")
        payload = {
            "alert_id": r.get("alert_id"),
            "triage_result": r.get("triage_result"),
            "finding": (r.get("finding") or "")[:500],
            "actionability": r.get("actionability"),
            "github_issue": r.get("github_issue"),
        }
        if not dry_run:
            session.add(
                SignalFired(
                    id=uuid.uuid4(),
                    task_id=None,
                    ticker=str(ticker)[:16] if ticker else None,
                    signal_type="historical_alert_triage",
                    urgency=urgency,
                    payload=payload,
                    fired_at=datetime.now(ET),
                )
            )
        report.alerts_imported += 1


def _scan_queue_state(path: Path, report: CopilotStateReport) -> None:
    try:
        with path.open() as f:
            data = yaml.safe_load(f)
    except Exception as e:
        log.warning("copilot_state.queue_read_fail", error=str(e))
        return
    report.files_read.append(path.as_posix())
    tasks = data.get("tasks") or {} if isinstance(data, dict) else {}
    for task_id, task in tasks.items():
        if not isinstance(task, dict):
            continue
        report.queue_items_considered += 1
        status = str(task.get("status", "")).lower()
        # Only items that were still active or recently done with meaningful work
        if status in ("done", "running") and task.get("summary"):
            title = str(task.get("title", task_id))[:120]
            report.queue_items_ready_for_investigation.append(f"#{task_id}: {title}")


async def import_copilot_state(
    session: AsyncSession,
    copilot_data_dir: Path,
    *,
    dry_run: bool = True,
) -> CopilotStateReport:
    """Read copilot state YAML, emit signals_fired rows (unless dry_run)."""
    report = CopilotStateReport()

    for f in sorted(copilot_data_dir.glob("filing_research_state_*.yaml")):
        await _import_filing_state_file(session, f, report, dry_run)

    analyst = copilot_data_dir / "analyst_state.yaml"
    if analyst.exists():
        await _import_analyst_state(session, analyst, report, dry_run)

    queue = copilot_data_dir / "queue_state.yaml"
    if queue.exists():
        _scan_queue_state(queue, report)

    return report
