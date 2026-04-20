"""refresh_backlinks — populate the `## Backlinks` section of every
theme / concept / people / question / investigation file with its
inbound wikilinks grouped by source type.

Pure Python graph traversal — no LLM calls. Cheap and safe to run
often (every few hours). Idempotent: each run replaces its own
previous `## Backlinks` section in-place; no double-appending.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from handlers import HandlerContext, HandlerResult
from praxis_core.logging import get_logger
from praxis_core.time_et import et_iso
from praxis_core.vault.writer import atomic_write

log = get_logger("handlers.refresh_backlinks")

WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+)(?:\|[^\]]+)?\]\]")
BACKLINKS_SECTION_RE = re.compile(
    r"(?ms)^##\s+Backlinks\b.*?(?=^##\s+|\Z)"
)

_SKIP_DIRS = {"_raw", "_analyzed", "_surfaced", "_backups", ".obsidian", ".cache"}

# Which directories get backlinks written to their files.
_GRAPH_TARGET_DIRS = ("themes", "concepts", "people", "questions", "investigations")


def _iter_notes(vault_root: Path):
    for p in vault_root.rglob("*.md"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        yield p


def _normalize_link(raw: str) -> str:
    """Normalize a wikilink target to match how it'd appear on disk.

    Strips anchor (#) and alias (|) fragments, drops trailing `.md`.
    Returns the path-or-stem form.
    """
    target = raw.strip().split("|", 1)[0].split("#", 1)[0].strip()
    if target.endswith(".md"):
        target = target[:-3]
    return target


def _classify_source(source_rel: str) -> str:
    """Group backlinks by origin so the Backlinks section is scannable.

    Uses the first path segment. `companies/<T>/notes.md` → companies;
    `companies/<T>/dives/<s>.md` → dives; `memos/<date>-...md` → memos.
    """
    parts = source_rel.split("/")
    if parts[0] == "companies" and len(parts) >= 3:
        if parts[2] == "dives":
            return "dives"
        if parts[2] == "memos":
            return "memos"
        if parts[2].startswith("notes.md"):
            return "companies"
        return "companies"
    if parts[0] in ("memos", "investigations", "questions", "themes", "concepts", "people"):
        return parts[0]
    return "other"


def build_backlink_graph(vault_root: Path) -> dict[str, list[tuple[str, str]]]:
    """{target_rel_no_ext: [(source_rel, classification), ...]}"""
    targets = {
        str(p.relative_to(vault_root).as_posix())[:-3]: []
        for p in _iter_notes(vault_root)
    }
    # Also match via filename-stem so `[[buffett-warren]]` resolves to
    # `people/buffett-warren.md` even when the wikilink omits the dir.
    stem_to_rel: dict[str, str] = {}
    for rel in targets:
        stem = Path(rel).name
        stem_to_rel.setdefault(stem, rel)

    graph: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for src_path in _iter_notes(vault_root):
        src_rel = str(src_path.relative_to(vault_root).as_posix())
        try:
            text = src_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Skip the managed Backlinks section itself so we don't count links
        # inside our own output.
        cleaned = BACKLINKS_SECTION_RE.sub("", text)
        seen_this_file: set[str] = set()
        for m in WIKILINK_RE.finditer(cleaned):
            target = _normalize_link(m.group(1))
            # Match exact path-no-ext first, fallback to stem
            resolved: str | None = None
            if target in targets:
                resolved = target
            elif Path(target).name in stem_to_rel:
                resolved = stem_to_rel[Path(target).name]
            if resolved is None:
                continue
            if resolved == src_rel[:-3]:
                continue  # self-link
            if resolved in seen_this_file:
                continue
            seen_this_file.add(resolved)
            graph[resolved].append((src_rel, _classify_source(src_rel)))
    return graph


def render_backlinks_section(inbound: list[tuple[str, str]], ran_at: str) -> str:
    """Produce the managed `## Backlinks` block for a file."""
    if not inbound:
        return (
            "## Backlinks\n\n"
            f"_Auto-refreshed {ran_at}. No inbound wikilinks yet._\n"
        )
    by_group: dict[str, list[str]] = defaultdict(list)
    for src, group in inbound:
        by_group[group].append(src)
    lines = ["## Backlinks", ""]
    lines.append(f"_Auto-refreshed {ran_at}. {len(inbound)} inbound wikilinks._")
    lines.append("")
    for group in ("companies", "dives", "memos", "investigations", "questions", "themes", "concepts", "people", "other"):
        hits = sorted(set(by_group.get(group, [])))
        if not hits:
            continue
        lines.append(f"### {group} ({len(hits)})")
        for src in hits:
            stem = Path(src).stem
            lines.append(f"- [[{src}|{stem}]]")
        lines.append("")
    return "\n".join(lines) + "\n"


def apply_backlinks(path: Path, inbound: list[tuple[str, str]], ran_at: str) -> bool:
    """Write the backlinks section to `path` in place. Returns True if
    the file changed (so callers can count updates)."""
    try:
        current = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    block = render_backlinks_section(inbound, ran_at)
    # Replace existing ## Backlinks section or append at end
    if BACKLINKS_SECTION_RE.search(current):
        updated = BACKLINKS_SECTION_RE.sub(block, current, count=1)
    else:
        updated = current.rstrip() + "\n\n" + block
    if updated == current:
        return False
    atomic_write(path, updated)
    return True


async def handle(ctx: HandlerContext) -> HandlerResult:
    vault = ctx.vault_root
    ran_at = et_iso()
    graph = build_backlink_graph(vault)

    updated = 0
    scanned = 0
    for target_dir in _GRAPH_TARGET_DIRS:
        base = vault / target_dir
        if not base.exists():
            continue
        for p in base.rglob("*.md"):
            scanned += 1
            rel_no_ext = str(p.relative_to(vault).as_posix())[:-3]
            inbound = graph.get(rel_no_ext, [])
            if apply_backlinks(p, inbound, ran_at):
                updated += 1

    log.info(
        "refresh_backlinks.done",
        scanned=scanned,
        updated=updated,
        total_graph_nodes=len(graph),
    )
    return HandlerResult(
        ok=True,
        message=f"refreshed {updated}/{scanned} files; graph has {len(graph)} nodes with inbound links",
    )
