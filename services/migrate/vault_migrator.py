"""Migrate the autoresearch vault into a v2 vault.

Two modes:
  - plan(): enumerate actions, produce a report. No writes.
  - apply(): execute. Uses atomic_write so crash-mid-migration leaves the target
    in a consistent state.

The trickiest parts:
  1. Multiple theses per ticker (30_theses/<handle>.md) need to merge into one thesis.md.
  2. Memos need re-nesting into companies/<TICKER>/memos/ when ticker is detectable.
  3. Sources need flattening from YYYY/MM hierarchy to YYYY-MM-DD.
  4. Wikilinks across all migrated files need rewriting via the rename map.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from praxis_core.logging import get_logger
from praxis_core.vault.writer import atomic_write
from services.migrate.frontmatter import process_markdown, serialize
from services.migrate.rename_map import (
    RenameMap,
    build_rename_map,
    discover_known_tickers,
)
from services.migrate.wikilinks import rewrite_body

log = get_logger("migrate.vault")


@dataclass
class VaultMigrationReport:
    source_root: Path
    target_root: Path
    entries_total: int = 0
    files_written: int = 0
    files_dropped: int = 0
    files_passthrough: int = 0  # kind=copy (needs review)
    thesis_merges: dict[str, list[str]] = field(
        default_factory=dict
    )  # ticker -> list of source paths
    memo_rensests: dict[str, list[str]] = field(
        default_factory=dict
    )  # ticker -> list of memo paths
    unresolved_wikilinks: list[tuple[str, str]] = field(
        default_factory=list
    )  # (source file, target)
    wikilinks_rewritten: int = 0

    def render(self) -> str:
        lines = [
            "# Vault migration report",
            "",
            f"Source: {self.source_root}",
            f"Target: {self.target_root}",
            "",
            "## Summary",
            f"- Files considered: {self.entries_total}",
            f"- Files written to target: {self.files_written}",
            f"- Files dropped (intentional): {self.files_dropped}",
            f"- Files passthrough (UNHANDLED — review): {self.files_passthrough}",
            f"- Wikilinks rewritten: {self.wikilinks_rewritten}",
            f"- Unresolved wikilinks: {len(self.unresolved_wikilinks)}",
            "",
        ]
        if self.thesis_merges:
            lines += ["## Thesis merges", ""]
            for ticker, sources in sorted(self.thesis_merges.items()):
                lines.append(f"- **{ticker}** ← {len(sources)} source file(s):")
                for s in sources:
                    lines.append(f"  - `{s}`")
                if len(sources) > 1:
                    lines.append(
                        f"  ⚠️ Multiple thesis files for {ticker} — merger appends all into "
                        f"companies/{ticker}/thesis.md, newest first. Review after apply."
                    )
            lines.append("")
        if self.memo_rensests:
            lines += ["## Memos re-nested by ticker", ""]
            for ticker, memos in sorted(self.memo_rensests.items()):
                lines.append(f"- **{ticker}**: {len(memos)} memo(s)")
            lines.append("")
        if self.unresolved_wikilinks:
            lines += [
                "## Unresolved wikilinks (⚠️ will be broken in target)",
                "",
                "These are wikilinks in source files whose target doesn't map to anything in the",
                "rename map. Often these are links to dropped content (agenda, current_focus) or",
                "typos in the source. Review each:",
                "",
            ]
            # Group by source
            by_source: dict[str, list[str]] = {}
            for src, tgt in self.unresolved_wikilinks:
                by_source.setdefault(src, []).append(tgt)
            for src in sorted(by_source):
                lines.append(f"- `{src}`")
                for tgt in sorted(set(by_source[src]))[:10]:
                    lines.append(f"  - `[[{tgt}]]`")
                if len(by_source[src]) > 10:
                    lines.append(f"  - ... and {len(by_source[src]) - 10} more")
            lines.append("")
        return "\n".join(lines)


def _process_and_write(
    source_path: Path,
    target_path: Path,
    rename_map: RenameMap,
    source_label: str,
    report: VaultMigrationReport,
) -> None:
    """Read source, normalize frontmatter, rewrite wikilinks, atomic-write to target."""
    text = source_path.read_text(encoding="utf-8", errors="replace")

    # Split FM + body, normalize FM
    if text.startswith("---"):
        metadata, body = process_markdown(text, source_label=source_label)
    else:
        metadata, body = {}, text
        # Add migration audit via fake frontmatter
        from praxis_core.time_et import et_iso

        metadata = {"migrated_from": source_label, "migrated_at": et_iso()}

    # Rewrite wikilinks in body
    result = rewrite_body(body, rename_map)
    report.wikilinks_rewritten += result.rewrote
    for target in result.unresolved:
        report.unresolved_wikilinks.append(
            (
                str(source_path.relative_to(source_path.parents[len(source_path.parents) - 2])),
                target,
            )
            if False
            else (source_path.as_posix(), target)
        )

    # Re-serialize
    final = serialize(metadata, result.new_body)
    atomic_write(target_path, final)


def _merge_theses_for_ticker(
    ticker: str,
    thesis_sources: list[Path],
    target: Path,
    rename_map: RenameMap,
    report: VaultMigrationReport,
) -> None:
    """Concatenate multiple thesis files into one, preserving order by mtime descending
    (newest first). Each source becomes a ## section inside the merged file.
    """
    thesis_sources = sorted(thesis_sources, key=lambda p: p.stat().st_mtime, reverse=True)
    report.thesis_merges[ticker] = [p.as_posix() for p in thesis_sources]

    from praxis_core.time_et import et_iso

    merged_meta = {
        "type": "thesis",
        "ticker": ticker,
        "status": "active",
        "data_vintage": et_iso()[:10],
        "migrated_from": "autoresearch",
        "migrated_at": et_iso(),
        "merged_from": [p.name for p in thesis_sources],
    }

    body_parts: list[str] = [f"# {ticker} thesis (merged)\n"]
    if len(thesis_sources) > 1:
        body_parts.append(
            f"> Merged from {len(thesis_sources)} source thesis files during v1→v2 migration.\n"
            "> Sections are ordered newest first by mtime. Review and consolidate by hand.\n"
        )

    for src in thesis_sources:
        text = src.read_text(encoding="utf-8", errors="replace")
        # Strip source's own frontmatter and heading
        if text.startswith("---"):
            end = text.find("\n---", 4)
            if end >= 0:
                text = text[end + 4 :].lstrip("\n")
        rewrite = rewrite_body(text, rename_map)
        report.wikilinks_rewritten += rewrite.rewrote
        for t in rewrite.unresolved:
            report.unresolved_wikilinks.append((src.as_posix(), t))
        body_parts.append(f"\n## Source: `{src.name}` (mtime {int(src.stat().st_mtime)})\n")
        body_parts.append(rewrite.new_body)

    final = serialize(merged_meta, "\n".join(body_parts))
    atomic_write(target, final)
    report.files_written += 1


def plan(source_root: Path, target_root: Path) -> tuple[RenameMap, VaultMigrationReport]:
    """Dry-run: compute the rename map, read every source file to count + find unresolved
    wikilinks, but don't write anything.
    """
    known_tickers = discover_known_tickers(source_root)
    rename_map = build_rename_map(source_root, known_tickers=known_tickers)
    report = VaultMigrationReport(source_root=source_root, target_root=target_root)
    report.entries_total = len(rename_map.entries)

    # Simulate body-read for wikilink analysis
    thesis_by_ticker: dict[str, list[Path]] = {}
    for entry in rename_map.entries:
        src = source_root / entry.old_path
        if entry.new_path is None:
            report.files_dropped += 1
            continue
        if entry.kind == "thesis_merge":
            ticker = entry.new_path.split("/")[1]
            thesis_by_ticker.setdefault(ticker, []).append(src)
            continue
        if entry.kind == "copy" and entry.old_path.endswith(".md"):
            report.files_passthrough += 1
        report.files_written += 1  # planned count

        if src.is_file() and entry.old_path.endswith(".md"):
            try:
                text = src.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            result = rewrite_body(text, rename_map)
            report.wikilinks_rewritten += result.rewrote
            for t in result.unresolved:
                report.unresolved_wikilinks.append((src.as_posix(), t))
            # Track memo re-nests
            if entry.kind == "memo" and entry.new_path.startswith("companies/"):
                ticker = entry.new_path.split("/")[1]
                report.memo_rensests.setdefault(ticker, []).append(entry.old_path)

    for ticker, sources in thesis_by_ticker.items():
        report.thesis_merges[ticker] = [s.as_posix() for s in sources]
        report.files_written += 1  # merged thesis

    return rename_map, report


def _seed_vault_root(target_root: Path) -> None:
    """D55 — copy vault_seed/CLAUDE.md, INDEX.md, LOG.md into target root.

    These are required for the running system: handlers call
    read_vault_schema() which reads CLAUDE.md; refresh_index rebuilds
    INDEX.md; compile_to_wiki + notify append to LOG.md.
    """
    from praxis_core.time_et import et_iso

    repo_root = Path(__file__).resolve().parent.parent.parent
    seed_dir = repo_root / "vault_seed"

    for fname in ("CLAUDE.md", "INDEX.md", "LOG.md"):
        src = seed_dir / fname
        dst = target_root / fname
        if src.is_file() and not dst.exists():
            import shutil as _sh

            dst.parent.mkdir(parents=True, exist_ok=True)
            _sh.copy2(src, dst)

    # Stamp migration marker
    marker = target_root / ".migrated"
    if not marker.exists():
        marker.write_text(f"migrated_at={et_iso()}\n")


def apply(source_root: Path, target_root: Path) -> VaultMigrationReport:
    """Execute the migration. Writes into target_root (should be empty or staging)."""
    _seed_vault_root(target_root)  # D55 — seed CLAUDE.md / INDEX.md / LOG.md first
    known_tickers = discover_known_tickers(source_root)
    rename_map = build_rename_map(source_root, known_tickers=known_tickers)
    report = VaultMigrationReport(source_root=source_root, target_root=target_root)
    report.entries_total = len(rename_map.entries)

    # Collect thesis merges first
    thesis_by_ticker: dict[str, list[Path]] = {}
    file_entries: list[tuple[Path, Path, str]] = []  # (src, target, kind)
    for entry in rename_map.entries:
        src = source_root / entry.old_path
        if entry.new_path is None:
            report.files_dropped += 1
            continue
        if entry.kind == "thesis_merge":
            ticker = entry.new_path.split("/")[1]
            thesis_by_ticker.setdefault(ticker, []).append(src)
            continue
        if entry.kind == "copy":
            report.files_passthrough += 1
        target = target_root / entry.new_path
        file_entries.append((src, target, entry.kind))

    # Pass 1: write each file with frontmatter normalization + wikilink rewrite
    for src, target, kind in file_entries:
        if not src.exists():
            continue
        if src.is_dir():
            continue
        if not src.name.endswith(".md"):
            # Binary / JSON / YAML passthrough — copy bytes
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            report.files_written += 1
            continue
        _process_and_write(src, target, rename_map, "autoresearch", report)
        report.files_written += 1
        if kind == "memo" and target.as_posix().startswith(target_root.as_posix() + "/companies/"):
            # companies/<TICKER>/memos/<file>
            rel_to_target = target.relative_to(target_root).as_posix()
            ticker = rel_to_target.split("/")[1]
            report.memo_rensests.setdefault(ticker, []).append(src.as_posix())

    # Pass 2: merge theses
    for ticker, sources in thesis_by_ticker.items():
        target = target_root / f"companies/{ticker}/thesis.md"
        _merge_theses_for_ticker(ticker, sources, target, rename_map, report)

    return report
