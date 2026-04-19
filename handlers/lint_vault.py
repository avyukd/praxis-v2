from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import frontmatter

from praxis_core.logging import get_logger
from praxis_core.schemas.artifacts import LintFinding, LintReport
from praxis_core.schemas.payloads import LintVaultPayload
from praxis_core.vault import conventions as vc
from praxis_core.vault.writer import atomic_write

from handlers import HandlerContext, HandlerResult

log = get_logger("handlers.lint_vault")


WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+)(?:\|[^\]]+)?\]\]")
_SKIP_DIRS = {"_raw", "_analyzed", ".obsidian", ".cache"}


def _iter_notes(vault_root: Path):
    for p in vault_root.rglob("*.md"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        yield p


def _resolve_wikilink(vault_root: Path, target: str) -> bool:
    target = target.strip()
    if target.startswith("#"):
        return True
    # Try direct path
    candidates = [
        vault_root / target,
        vault_root / (target + ".md"),
    ]
    # Try finding by stem match anywhere (basic fuzzy)
    stem = target.split("/")[-1].replace(".md", "")
    if stem:
        for note in _iter_notes(vault_root):
            if note.stem == stem:
                candidates.append(note)
                break
    return any(c.exists() for c in candidates)


async def handle(ctx: HandlerContext) -> HandlerResult:
    LintVaultPayload.model_validate(ctx.payload)

    findings: list[LintFinding] = []
    notes = list(_iter_notes(ctx.vault_root))
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
            if not _resolve_wikilink(ctx.vault_root, target):
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

    report = LintReport(
        ran_at=datetime.now(timezone.utc).isoformat(),
        findings=findings,
        vault_stats=stats,
    )

    report_path = (
        ctx.vault_root / "journal" / f"{datetime.utcnow().strftime('%Y-%m-%d')}-lint.md"
    )
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
