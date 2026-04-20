"""Backfill praxis-copilot S3 event log into praxis-v2 events table (D59).

S3 layout: `s3://praxis-copilot/data/events/YYYY-MM-DD/evt-<id>.json`

Example event:
    {
      "event_id": "evt-4634f590dc86",
      "timestamp": "2026-04-07T12:38:24+00:00",
      "source": "sec-filings-extractor",
      "ticker": "PROP",
      "cik": "1162896",
      "data_type": "filings:8-K",
      "s3_path": "data/raw/filings/1162896/000114036126013492/extracted.json",
      "monitors_triggered": [...]
    }

Translated into our `events` table as `event_type='filing_ingested_historical'`
(or `release_ingested_historical`) with the full copilot payload stored in
`payload`, so surface_ideas + dashboards can query cross-source history
without conflating with live-source events.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime

import boto3
from sqlalchemy import text

from praxis_core.db.session import session_scope
from praxis_core.logging import get_logger

log = get_logger("migrate.copilot_events")

BUCKET = "praxis-copilot"
EVENTS_PREFIX = "data/events/"


@dataclass
class EventsImportReport:
    considered: int = 0
    imported: int = 0
    skipped_existing: int = 0
    skipped_malformed: int = 0
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "# praxis-copilot events backfill",
            "",
            f"- Considered: {self.considered}",
            f"- Imported: {self.imported}",
            f"- Skipped (existing): {self.skipped_existing}",
            f"- Skipped (malformed): {self.skipped_malformed}",
        ]
        if self.errors:
            lines.append(f"- Errors ({len(self.errors)}), first 10:")
            for e in self.errors[:10]:
                lines.append(f"  - {e}")
        return "\n".join(lines)


def _new_s3_client():
    return boto3.client("s3")


def _list_event_keys(s3) -> list[str]:
    keys: list[str] = []
    token = None
    while True:
        kwargs = {"Bucket": BUCKET, "Prefix": EVENTS_PREFIX}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []) or []:
            if obj["Key"].endswith(".json"):
                keys.append(obj["Key"])
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return keys


def _get_json(s3, key: str) -> dict | None:
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        return json.loads(obj["Body"].read().decode("utf-8"))
    except Exception:
        return None


def _classify_data_type(data_type: str) -> str:
    """Map copilot data_type → our event_type naming."""
    d = (data_type or "").lower()
    if "press" in d or "press_release" in d:
        return "release_ingested_historical"
    return "filing_ingested_historical"


async def _import_event(
    s3, key: str, seen_event_ids: set[str], report: EventsImportReport
) -> None:
    report.considered += 1
    evt = await asyncio.to_thread(_get_json, s3, key)
    if not evt:
        report.skipped_malformed += 1
        return

    eid = evt.get("event_id") or key
    if eid in seen_event_ids:
        report.skipped_existing += 1
        return

    ts_raw = evt.get("timestamp")
    if not ts_raw:
        report.skipped_malformed += 1
        return
    try:
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except ValueError:
        report.skipped_malformed += 1
        return

    event_type = _classify_data_type(evt.get("data_type") or "")

    async with session_scope() as session:
        # Check if already imported (dedup on payload->>'event_id')
        existing = await session.execute(
            text(
                """
                SELECT id FROM events
                WHERE component = 'migrate.copilot_events'
                  AND payload->>'event_id' = :eid
                LIMIT 1
                """
            ),
            {"eid": eid},
        )
        if existing.first() is not None:
            report.skipped_existing += 1
            seen_event_ids.add(eid)
            return
        await session.execute(
            text(
                """
                INSERT INTO events (ts, component, event_type, payload)
                VALUES (:ts, 'migrate.copilot_events', :event_type, CAST(:payload AS jsonb))
                """
            ),
            {
                "ts": ts,
                "event_type": event_type,
                "payload": json.dumps(evt),
            },
        )
    seen_event_ids.add(eid)
    report.imported += 1


async def run_events_backfill(
    *,
    concurrency: int = 32,
    limit: int | None = None,
) -> EventsImportReport:
    report = EventsImportReport()
    s3 = _new_s3_client()

    keys = await asyncio.to_thread(_list_event_keys, s3)
    log.info("migrate.copilot_events.count", n=len(keys))
    if limit:
        keys = keys[:limit]

    # Preload existing event_ids to cut DB hits
    async with session_scope() as session:
        res = await session.execute(
            text(
                """
                SELECT payload->>'event_id' FROM events
                WHERE component = 'migrate.copilot_events'
                  AND payload ? 'event_id'
                """
            )
        )
        seen: set[str] = {row[0] for row in res if row[0]}
    log.info("migrate.copilot_events.already_imported", n=len(seen))

    sem = asyncio.Semaphore(concurrency)

    async def _with_sem(coro):
        async with sem:
            try:
                await coro
            except Exception as e:
                report.errors.append(str(e)[:300])

    tasks = [_with_sem(_import_event(s3, k, seen, report)) for k in keys]
    chunk = max(50, len(tasks) // 20) if tasks else 0
    for i in range(0, len(tasks), chunk or 1):
        await asyncio.gather(*tasks[i : i + (chunk or 1)])
        log.info(
            "migrate.copilot_events.progress",
            done=min(i + (chunk or 1), len(tasks)),
            total=len(tasks),
            imported=report.imported,
        )

    return report
