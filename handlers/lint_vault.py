from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from handlers import HandlerContext, HandlerResult
from praxis_core.logging import get_logger
from praxis_core.schemas.artifacts import LintFinding, LintReport
from praxis_core.schemas.payloads import LintVaultPayload
from praxis_core.time_et import et_date_str, et_iso
from praxis_core.vault.writer import atomic_write

log = get_logger("handlers.lint_vault")


WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+)(?:\|[^\]]+)?\]\]")
_SKIP_DIRS = {"_raw", "_analyzed", ".obsidian", ".cache"}


@dataclass(frozen=True)
class _NoteIndex:
    notes: list[Path]
    relpaths: set[str]
    stems: set[str]


def _iter_notes(vault_root: Path):
    for p in vault_root.rglob("*.md"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        yield p


def _build_note_index(vault_root: Path) -> _NoteIndex:
    notes = list(_iter_notes(vault_root))
    relpaths = {p.relative_to(vault_root).as_posix() for p in notes}
    stems = {p.stem for p in notes}
    return _NoteIndex(notes=notes, relpaths=relpaths, stems=stems)


def _resolve_wikilink(index: _NoteIndex, target: str) -> bool:
    cleaned = target.strip()
    if not cleaned or cleaned.startswith("#"):
        return True

    path_part = cleaned.split("#", 1)[0].strip()
    if not path_part:
        return True

    direct_candidates = {path_part}
    if not path_part.endswith(".md"):
        direct_candidates.add(f"{path_part}.md")
    if any(candidate in index.relpaths for candidate in direct_candidates):
        return True

    stem = path_part.rsplit("/", 1)[-1].removesuffix(".md")
    return stem in index.stems


async def handle(ctx: HandlerContext) -> HandlerResult:
    LintVaultPayload.model_validate(ctx.payload)

    findings: list[LintFinding] = []
    index = _build_note_index(ctx.vault_root)
    notes = index.notes
    inbound: dict[str, int] = {}
    stats = {"total_notes": len(notes), "checked_links": 0}

    for note in notes:
        try:
            text = note.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        try:
            post = frontmatter.loads(text)
            if not post.metadata and not text.startswith("# ") and not text.startswith("---"):
                findings.append(
                    LintFinding(
                        severity="warning",
                        kind="missing_frontmatter",
                        path=str(note.relative_to(ctx.vault_root)),
                        description="no frontmatter and no heading",
                    )
                )
        except Exception as e:
            findings.append(
                LintFinding(
                    severity="error",
                    kind="missing_frontmatter",
                    path=str(note.relative_to(ctx.vault_root)),
                    description=f"frontmatter parse error: {e}",
                )
            )

        for match in WIKILINK_RE.finditer(text):
            target = match.group(1)
            stats["checked_links"] += 1
            inbound[target] = inbound.get(target, 0) + 1
            if not _resolve_wikilink(index, target):
                findings.append(
                    LintFinding(
                        severity="error",
                        kind="broken_wikilink",
                        path=str(note.relative_to(ctx.vault_root)),
                        description=f"broken wikilink: [[{target}]]",
                    )
                )

    # Orphan detection — notes outside firehose dirs with no inbound links
    for note in notes:
        rel = note.relative_to(ctx.vault_root).as_posix()
        stem = note.stem
        top = rel.split("/", 1)[0] if "/" in rel else rel
        if top in {"INDEX.md", "CLAUDE.md", "LOG.md", "README.md", "AGENDA.md"}:
            continue
        if top in {"_raw", "_analyzed", "journal"}:
            continue
        if inbound.get(stem, 0) == 0 and inbound.get(rel, 0) == 0:
            findings.append(
                LintFinding(
                    severity="warning",
                    kind="orphan_note",
                    path=rel,
                    description="no inbound wikilinks detected",
                )
            )

    # Orphan raw-filing detection: _raw/filings/.../<accession>/filing.txt with no
    # corresponding _analyzed/ entry. These are filings we ingested but never analyzed —
    # likely a dead-letter victim or a stuck pipeline.
    raw_filings = ctx.vault_root / "_raw" / "filings"
    if raw_filings.exists():
        for form_dir in raw_filings.iterdir():
            if not form_dir.is_dir():
                continue
            for acc_dir in form_dir.iterdir():
                if not acc_dir.is_dir():
                    continue
                analyzed_dir = (
                    ctx.vault_root / "_analyzed" / "filings" / form_dir.name / acc_dir.name
                )
                # Consider analyzed if either triage or analysis artifact exists
                has_analysis = any(
                    (analyzed_dir / f).exists()
                    for f in ("analysis.md", "signals.json", "triage.md", "triage.json")
                )
                if not has_analysis:
                    findings.append(
                        LintFinding(
                            severity="warning",
                            kind="stale_active_note",
                            path=str(acc_dir.relative_to(ctx.vault_root)),
                            description=(
                                "raw filing has no _analyzed/ artifact — "
                                "possibly dead-lettered or stuck pipeline"
                            ),
                        )
                    )
    stats["raw_filings_orphaned"] = sum(
        1 for f in findings if f.kind == "stale_active_note" and "raw filing" in f.description
    )

    report = LintReport(
        ran_at=et_iso(),
        findings=findings,
        vault_stats=stats,
    )

    report_path = ctx.vault_root / "journal" / f"{et_date_str()}-lint.md"
    body_lines = [
        f"# Lint report — {report.ran_at}",
        "",
        f"Total notes: {stats['total_notes']}",
        f"Checked wikilinks: {stats['checked_links']}",
        f"Findings: {len(findings)}",
        "",
    ]
    for sev in ("error", "warning", "info"):
        matched = [f for f in findings if f.severity == sev]
        if not matched:
            continue
        body_lines.append(f"## {sev.title()} ({len(matched)})")
        for f in matched:
            body_lines.append(f"- **{f.kind}** `{f.path}` — {f.description}")
        body_lines.append("")

    atomic_write(report_path, "\n".join(body_lines))
    log.info("lint_vault.done", findings=len(findings), notes=len(notes))
    return HandlerResult(ok=True, message=f"lint report written ({len(findings)} findings)")
