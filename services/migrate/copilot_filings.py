"""Backfill praxis-copilot S3 analyses into praxis-v2 vault (Section E D58).

Two prefixes, one translator per prefix:

- `s3://praxis-copilot/data/raw/filings/<cik>/<accession>/`
    → `vault/_analyzed/filings/<form>/<accession>/analysis.{md,json}` +
      `vault/_raw/filings/<form>/<accession>/filing.txt`
- `s3://praxis-copilot/data/raw/press_releases/<source>/<ticker>/<release_id>/`
    → `vault/_analyzed/press_releases/<source>/<ticker>/<release_id>/analysis.{md,json}` +
      `vault/_raw/press_releases/<source>/<ticker>/<release_id>/release.txt`

Copilot's analysis.json schema:
    { classification: UPPER, magnitude: float, new_information, materiality,
      explanation, analyzed_at, analyzer }

Our AnalysisResult requires additional fields (accession, ticker, form_type,
source, model) which come from the sibling index.json. Classification is
lowercased; model='copilot-migrated' marks the origin.

Also populates `sources` rows keyed on our `filing:<acc>` / `pr:<src>:<rid>`
dedup_keys so the live pollers won't re-ingest these.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from sqlalchemy.dialects.postgresql import insert

from praxis_core.db.models import Source
from praxis_core.db.session import session_scope
from praxis_core.logging import get_logger
from praxis_core.time_et import et_iso
from praxis_core.vault import conventions as vc
from praxis_core.vault.writer import atomic_write

log = get_logger("migrate.copilot_filings")

BUCKET = "praxis-copilot"
FILINGS_PREFIX = "data/raw/filings/"
PRESS_PREFIX = "data/raw/press_releases/"

CLASSIFICATION_MAP = {
    "POSITIVE": "positive",
    "NEGATIVE": "negative",
    "NEUTRAL": "neutral",
}


@dataclass
class ImportReport:
    filings_considered: int = 0
    filings_imported: int = 0
    filings_skipped_existing: int = 0
    filings_skipped_malformed: int = 0
    press_considered: int = 0
    press_imported: int = 0
    press_skipped_existing: int = 0
    press_skipped_malformed: int = 0
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        parts = [
            "# praxis-copilot S3 backfill",
            "",
            f"- Filings considered: {self.filings_considered}",
            f"- Filings imported: {self.filings_imported}",
            f"- Filings skipped (existing): {self.filings_skipped_existing}",
            f"- Filings skipped (malformed): {self.filings_skipped_malformed}",
            f"- Press releases considered: {self.press_considered}",
            f"- Press releases imported: {self.press_imported}",
            f"- Press releases skipped (existing): {self.press_skipped_existing}",
            f"- Press releases skipped (malformed): {self.press_skipped_malformed}",
        ]
        if self.errors:
            parts.append("")
            parts.append(f"## Errors ({len(self.errors)} first 20 shown)")
            for e in self.errors[:20]:
                parts.append(f"- {e}")
        return "\n".join(parts)


# ----------------------------------------------------------------------
# S3 helpers
# ----------------------------------------------------------------------


def _new_s3_client():
    return boto3.client("s3")


def _list_analysis_keys(s3, prefix: str) -> list[str]:
    """List every analysis.json under the prefix."""
    keys: list[str] = []
    token = None
    while True:
        kwargs = {"Bucket": BUCKET, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []) or []:
            if obj["Key"].endswith("/analysis.json"):
                keys.append(obj["Key"])
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return keys


def _get_object(s3, key: str) -> bytes | None:
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        return obj["Body"].read()
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchKey":
            return None
        raise


def _get_json(s3, key: str) -> dict | None:
    raw = _get_object(s3, key)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


# ----------------------------------------------------------------------
# Translation
# ----------------------------------------------------------------------


def _translate_analysis(
    analysis: dict,
    accession: str,
    form_type: str,
    ticker: str | None,
    source: str,
) -> dict:
    """Copilot → AnalysisResult JSON."""
    cls_raw = (analysis.get("classification") or "").upper()
    classification = CLASSIFICATION_MAP.get(cls_raw, "neutral")
    mag = analysis.get("magnitude")
    try:
        magnitude = max(0.0, min(1.0, float(mag))) if mag is not None else 0.0
    except (TypeError, ValueError):
        magnitude = 0.0
    return {
        "accession": accession,
        "ticker": ticker,
        "form_type": form_type,
        "source": source,
        "classification": classification,
        "magnitude": magnitude,
        "new_information": (analysis.get("new_information") or "").strip() or "(no content)",
        "materiality": (analysis.get("materiality") or "").strip() or "(no content)",
        "explanation": (analysis.get("explanation") or "").strip() or "(no content)",
        "analyzed_at": analysis.get("analyzed_at") or et_iso(),
        "model": "copilot-migrated",
    }


def _analysis_md(translated: dict) -> str:
    """Render the markdown sibling of analysis.json, matching the live
    analyze_filing handler's output format."""
    trade_relevant = (
        translated["magnitude"] >= 0.5
        and translated["classification"] in ("positive", "neutral")
    )
    lines = [
        "---",
        "type: analysis",
        f"accession: {translated['accession']}",
        f"ticker: {translated['ticker'] or 'UNKNOWN'}",
        f"form_type: {translated['form_type']}",
        f"source: {translated['source']}",
        f"classification: {translated['classification']}",
        f"magnitude: {translated['magnitude']}",
        f"trade_relevant: {str(trade_relevant).lower()}",
        f"analyzed_at: {translated['analyzed_at']}",
        "model: copilot-migrated",
        "migrated_at: " + et_iso(),
        "---",
        "",
        f"# Analysis — {translated['ticker'] or 'UNKNOWN'} / {translated['form_type']} "
        f"({translated['accession']})",
        "",
        "## New information",
        translated["new_information"],
        "",
        "## Materiality",
        translated["materiality"],
        "",
        "## Explanation",
        translated["explanation"],
    ]
    return "\n".join(lines) + "\n"


def _signals_json(translated: dict) -> dict:
    """Mirror our live analyze_filing signals.json shape."""
    trade_relevant = (
        translated["magnitude"] >= 0.5
        and translated["classification"] in ("positive", "neutral")
    )
    return {
        "classification": translated["classification"],
        "magnitude": translated["magnitude"],
        "trade_relevant": trade_relevant,
        "ticker": translated["ticker"],
        "accession": translated["accession"],
        "form_type": translated["form_type"],
        "source": translated["source"],
        "analyzed_at": translated["analyzed_at"],
        "model": "copilot-migrated",
    }


# ----------------------------------------------------------------------
# Per-item import
# ----------------------------------------------------------------------


async def _import_filing(
    s3, key: str, vault_root: Path, report: ImportReport
) -> None:
    """Key shape: data/raw/filings/<cik>/<accession>/analysis.json"""
    report.filings_considered += 1
    try:
        parts = key.split("/")
        accession_raw = parts[-2]
        cik = parts[-3]
    except IndexError:
        report.filings_skipped_malformed += 1
        return
    accession = _normalize_accession(accession_raw)
    if accession is None:
        report.filings_skipped_malformed += 1
        return

    prefix = "/".join(parts[:-1]) + "/"
    analysis = await asyncio.to_thread(_get_json, s3, key)
    index = await asyncio.to_thread(_get_json, s3, prefix + "index.json")
    if not analysis or not index:
        report.filings_skipped_malformed += 1
        return

    form_type = (index.get("form_type") or "").strip()
    ticker_raw = index.get("ticker") or None
    # Sources.ticker is VARCHAR(16); copilot occasionally carries a longer
    # value (composite ticker, company name in the wrong field). Truncate.
    ticker = (ticker_raw[:16] if ticker_raw else None) or None
    if not form_type:
        report.filings_skipped_malformed += 1
        return

    analyzed_dir = vc.analyzed_filing_dir(vault_root, form_type, accession)
    analysis_md_path = analyzed_dir / "analysis.md"
    analysis_json_path = analyzed_dir / "analysis.json"
    signals_path = analyzed_dir / "signals.json"
    if analysis_md_path.exists() and analysis_json_path.exists():
        report.filings_skipped_existing += 1
        return

    translated = _translate_analysis(
        analysis,
        accession=accession,
        form_type=form_type,
        ticker=ticker,
        source="edgar",
    )
    md = _analysis_md(translated)
    signals = _signals_json(translated)

    analyzed_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(analysis_md_path, md)
    atomic_write(analysis_json_path, json.dumps(translated, indent=2))
    atomic_write(signals_path, json.dumps(signals, indent=2))

    # Also pull the raw form doc so dives can cite it.
    raw_dir = vc.raw_filing_dir(vault_root, form_type, accession)
    raw_dir.mkdir(parents=True, exist_ok=True)
    filing_txt = raw_dir / "filing.txt"
    if not filing_txt.exists():
        primary = (index.get("primary_doc") or "").strip()
        raw_body = None
        if primary:
            raw_body = await asyncio.to_thread(_get_object, s3, prefix + primary)
        if raw_body:
            try:
                atomic_write(filing_txt, raw_body.decode("utf-8", errors="replace"))
            except OSError:
                pass
        meta_json = raw_dir / "meta.json"
        if not meta_json.exists():
            meta = {
                "accession": accession,
                "form_type": form_type,
                "cik": cik,
                "ticker": ticker,
                "title": index.get("company_name") or "",
                "link": "",
                "items": index.get("items_detected") or [],
                "market_cap_usd": index.get("market_cap"),
                "filed_date": index.get("filed_date"),
                "source": "copilot-migrated",
                "ingested_at": et_iso(),
            }
            atomic_write(meta_json, json.dumps(meta, indent=2))

    # Seen-set entry so the live EDGAR poller doesn't re-triage this
    rel_raw = str(filing_txt.relative_to(vault_root)) if filing_txt.exists() else ""
    async with session_scope() as session:
        stmt = (
            insert(Source)
            .values(
                dedup_key=f"filing:{accession}",
                source_type=f"filing_{form_type.lower().replace('-', '_')}",
                vault_path=rel_raw,
                ticker=ticker,
                extra={
                    "accession": accession,
                    "form_type": form_type,
                    "cik": cik,
                    "ticker": ticker,
                    "market_cap_usd": index.get("market_cap"),
                    "filed_date": index.get("filed_date"),
                    "source": "copilot-migrated",
                    "migrated_at": et_iso(),
                },
            )
            .on_conflict_do_nothing(index_elements=[Source.dedup_key])
        )
        await session.execute(stmt)

    report.filings_imported += 1


async def _import_press(
    s3, key: str, vault_root: Path, report: ImportReport
) -> None:
    """Key shape: data/raw/press_releases/<source>/<ticker>/<release_id>/analysis.json"""
    report.press_considered += 1
    parts = key.split("/")
    if len(parts) < 7:
        report.press_skipped_malformed += 1
        return
    release_id = parts[-2]
    ticker = parts[-3][:16] if parts[-3] else None
    src_name = parts[-4]

    prefix = "/".join(parts[:-1]) + "/"
    analysis = await asyncio.to_thread(_get_json, s3, key)
    index = await asyncio.to_thread(_get_json, s3, prefix + "index.json")
    if not analysis or not index:
        report.press_skipped_malformed += 1
        return
    if not ticker:
        report.press_skipped_malformed += 1
        return

    analyzed_dir = vc.analyzed_pr_dir(vault_root, src_name, ticker, release_id)
    analysis_md_path = analyzed_dir / "analysis.md"
    analysis_json_path = analyzed_dir / "analysis.json"
    signals_path = analyzed_dir / "signals.json"
    if analysis_md_path.exists() and analysis_json_path.exists():
        report.press_skipped_existing += 1
        return

    translated = _translate_analysis(
        analysis,
        accession=release_id,
        form_type="press_release",
        ticker=ticker,
        source=src_name,
    )
    md = _analysis_md(translated)
    signals = _signals_json(translated)

    analyzed_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(analysis_md_path, md)
    atomic_write(analysis_json_path, json.dumps(translated, indent=2))
    atomic_write(signals_path, json.dumps(signals, indent=2))

    # Raw release body
    raw_dir = vc.raw_pr_dir(vault_root, src_name, ticker, release_id)
    raw_dir.mkdir(parents=True, exist_ok=True)
    release_txt = raw_dir / "release.txt"
    if not release_txt.exists():
        body = await asyncio.to_thread(_get_object, s3, prefix + "release.txt")
        if body:
            try:
                atomic_write(release_txt, body.decode("utf-8", errors="replace"))
            except OSError:
                pass

    idx_meta = raw_dir / "index.json"
    if not idx_meta.exists():
        atomic_write(
            idx_meta,
            json.dumps(
                {
                    "release_id": release_id,
                    "ticker": ticker,
                    "source": src_name,
                    "exchange": index.get("exchange"),
                    "title": index.get("headline"),
                    "url": index.get("url"),
                    "published_at": index.get("published_at"),
                    "ingested_at": et_iso(),
                    "migrated_from": "copilot",
                },
                indent=2,
            ),
        )

    # Seen-set entry so press pollers don't re-ingest
    rel_raw = str(release_txt.relative_to(vault_root)) if release_txt.exists() else ""
    async with session_scope() as session:
        stmt = (
            insert(Source)
            .values(
                dedup_key=f"pr:{src_name}:{release_id}",
                source_type=f"press_release_{src_name}",
                vault_path=rel_raw,
                ticker=ticker,
                extra={
                    "release_id": release_id,
                    "ticker": ticker,
                    "source": src_name,
                    "exchange": index.get("exchange"),
                    "title": index.get("headline"),
                    "url": index.get("url"),
                    "published_at": index.get("published_at"),
                    "source_tier": "copilot-migrated",
                    "migrated_at": et_iso(),
                },
            )
            .on_conflict_do_nothing(index_elements=[Source.dedup_key])
        )
        await session.execute(stmt)

    report.press_imported += 1


# ----------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------


_SANE_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_UNDASHED_ACCESSION = re.compile(r"^\d{18}$")


def _normalize_accession(raw: str) -> str | None:
    """Copilot's S3 uses both dashed (0001140361-26-008484) and undashed
    (000114036126008484) accession formats. Normalize to the dashed form
    we use everywhere else."""
    if _SANE_ACCESSION.match(raw):
        return raw
    if _UNDASHED_ACCESSION.match(raw):
        return f"{raw[0:10]}-{raw[10:12]}-{raw[12:18]}"
    return None


async def run_backfill(
    vault_root: Path,
    *,
    concurrency: int = 16,
    limit_filings: int | None = None,
    limit_press: int | None = None,
    skip_filings: bool = False,
    skip_press: bool = False,
) -> ImportReport:
    report = ImportReport()
    s3 = _new_s3_client()

    sem = asyncio.Semaphore(concurrency)

    async def _with_sem(coro):
        async with sem:
            try:
                await coro
            except Exception as e:
                report.errors.append(str(e)[:300])

    if not skip_filings:
        filing_keys = await asyncio.to_thread(_list_analysis_keys, s3, FILINGS_PREFIX)
        log.info("migrate.copilot_filings.count", n=len(filing_keys))
        # Accession sanity filter (covers both dashed + undashed copilot formats)
        filing_keys = [
            k for k in filing_keys if _normalize_accession(k.split("/")[-2]) is not None
        ]
        if limit_filings:
            filing_keys = filing_keys[:limit_filings]

        tasks = [_with_sem(_import_filing(s3, k, vault_root, report)) for k in filing_keys]
        # Progress logging in chunks
        chunk = max(50, len(tasks) // 20) if tasks else 0
        for i in range(0, len(tasks), chunk or 1):
            await asyncio.gather(*tasks[i : i + (chunk or 1)])
            log.info(
                "migrate.copilot_filings.progress",
                done=min(i + (chunk or 1), len(tasks)),
                total=len(tasks),
                imported=report.filings_imported,
                skipped_existing=report.filings_skipped_existing,
                malformed=report.filings_skipped_malformed,
            )

    if not skip_press:
        press_keys = await asyncio.to_thread(_list_analysis_keys, s3, PRESS_PREFIX)
        log.info("migrate.copilot_press.count", n=len(press_keys))
        if limit_press:
            press_keys = press_keys[:limit_press]
        tasks = [_with_sem(_import_press(s3, k, vault_root, report)) for k in press_keys]
        chunk = max(50, len(tasks) // 20) if tasks else 0
        for i in range(0, len(tasks), chunk or 1):
            await asyncio.gather(*tasks[i : i + (chunk or 1)])
            log.info(
                "migrate.copilot_press.progress",
                done=min(i + (chunk or 1), len(tasks)),
                total=len(tasks),
                imported=report.press_imported,
                skipped_existing=report.press_skipped_existing,
                malformed=report.press_skipped_malformed,
            )

    return report
