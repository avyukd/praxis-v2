"""Compute the v1 → v2 path rename map for autoresearch vault content.

The rename map is a plain dict: `old_relative_path (without .md) → new_relative_path (without .md)`.
Used both for physically moving files and for rewriting wikilinks (which reference paths-sans-.md).

Strategy:
  - `10_themes/X.md` → `themes/X.md`
  - `15_concepts/X.md` → `concepts/X.md`
  - `25_people/X.md` → `people/X.md`
  - `60_questions/X.md` → `questions/X.md`
  - `20_companies/TICKER/notes.md` → `companies/TICKER/notes.md`
  - `20_companies/TICKER/journal.md` → `companies/TICKER/journal.md`
  - `20_companies/TICKER/data/*` → `companies/TICKER/data/*`
  - `30_theses/<handle>.md` → determine ticker from frontmatter `ticker:` field; target
    `companies/<TICKER>/thesis.md`. (Merger logic applied upstream.)
  - `40_memos/<date>-<ticker>-<handle>.md` → `companies/<TICKER>/memos/<filename>` if ticker known,
    else `memos/<filename>` (top-level cross-cutting).
  - `80_sources/YYYY/MM/<file>.md` → `_raw/desktop_clips/YYYY-MM-<dd>/<file>.md` (preserve date
    from filename prefix if present, else use month-only).
  - Drop: `00_inbox/`, `50_journal/`, `70_signals/`, `90_meta/`, `99_development/`, `INDEX.md`,
    `.cache/`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RenameEntry:
    old_path: str  # relative path, no leading slash
    new_path: str | None  # None = drop
    kind: str  # "company_note"|"thesis_merge"|"memo"|"theme"|"concept"|"person"|"question"|"source"|"drop"|"copy"
    note: str = ""


@dataclass
class RenameMap:
    entries: list[RenameEntry] = field(default_factory=list)
    # old → new lookup of paths WITHOUT .md extension (for wikilink rewriting).
    stem_map: dict[str, str] = field(default_factory=dict)
    # slug lookup: just-the-filename-stem → new full path stem.
    # Used for wikilinks that reference files by stem only.
    slug_map: dict[str, str] = field(default_factory=dict)

    def add(self, entry: RenameEntry) -> None:
        self.entries.append(entry)
        if entry.new_path is not None and entry.old_path.endswith(".md"):
            old_stem = entry.old_path[:-3]
            new_stem = entry.new_path[:-3] if entry.new_path.endswith(".md") else entry.new_path
            self.stem_map[old_stem] = new_stem
            slug = Path(old_stem).name
            if slug and slug not in self.slug_map:
                self.slug_map[slug] = new_stem

    def lookup(self, target: str) -> str | None:
        """Look up a wikilink target.

        Wikilinks can be:
          - full relative path (`20_companies/NVDA/notes`)
          - stem only (`notes` — ambiguous but common in Obsidian)
          - with .md appended
        """
        target = target.strip()
        if target.endswith(".md"):
            target = target[:-3]
        if target in self.stem_map:
            return self.stem_map[target]
        # Fall back to slug match (filename only)
        return self.slug_map.get(Path(target).name)


# Paths we drop entirely.
_DROP_PREFIXES: tuple[str, ...] = (
    "00_inbox/",
    "50_journal/",
    "70_signals/",
    "90_meta/",
    "99_development/",
    ".cache/",
    ".obsidian/",
)
_DROP_FILES: frozenset[str] = frozenset(["INDEX.md"])


def _is_dropped(rel_path: str) -> bool:
    return rel_path in _DROP_FILES or any(rel_path.startswith(p) for p in _DROP_PREFIXES)


_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-")
# Memo filename patterns: "YYYY-MM-DD-<ticker-lower>-<handle>.md"
# We extract the ticker heuristically by trying the first hyphen-segment after the date.


def _memo_ticker_candidate(filename_stem: str, known_tickers: set[str]) -> str | None:
    # filename_stem like "2026-04-10-clmt-bull-test-forward"
    m = _DATE_RE.match(filename_stem)
    if not m:
        return None
    after_date = filename_stem[len(m.group(0)) :]
    # Try first 1-6 chars as ticker; prefer longer matches from known_tickers
    segments = after_date.split("-")
    for take in range(min(4, len(segments)), 0, -1):
        candidate = "-".join(segments[:take]).upper()
        # Normalize common ticker with dots/dashes
        if candidate in known_tickers:
            return candidate
    return None


def _read_frontmatter_ticker(path: Path) -> str | None:
    """Extract `ticker:` field from YAML frontmatter, if present."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    fm = text[4:end]
    m = re.search(r"^ticker:\s*['\"]?([A-Z][A-Z0-9\.\-]*)['\"]?\s*$", fm, re.MULTILINE)
    return m.group(1) if m else None


_SOURCE_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[_-]")


def _source_target_path(old_rel: str) -> str:
    """80_sources/2026/04/2026-04-18_ft.com_slug.md → _raw/desktop_clips/2026-04-18/ft.com_slug.md

    If no leading date in filename, use YYYY-MM from path + 01 as day placeholder.
    """
    parts = old_rel.split("/")
    # Expect 80_sources / YYYY / MM / filename.md
    filename = parts[-1]
    stem = filename[:-3] if filename.endswith(".md") else filename
    m = _SOURCE_DATE_RE.match(stem)
    if m:
        date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        remaining = stem[m.end() :]
        new_stem = remaining.strip("_-") or "source"
    else:
        # Fall back to YYYY-MM-01 using path components
        yy = parts[1] if len(parts) > 2 else "0000"
        mm = parts[2] if len(parts) > 3 else "00"
        date = f"{yy}-{mm}-01"
        new_stem = stem
    return f"_raw/desktop_clips/{date}/{new_stem}.md"


def build_rename_map(source_root: Path, *, known_tickers: set[str]) -> RenameMap:
    """Walk the autoresearch vault and compute planned renames.

    `known_tickers` — set of uppercased ticker symbols we have first-class company folders for.
    Used to resolve memo filenames to their target company folder.
    """
    rename_map = RenameMap()

    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source_root).as_posix()

        if _is_dropped(rel):
            rename_map.add(RenameEntry(old_path=rel, new_path=None, kind="drop"))
            continue

        # Company content
        if rel.startswith("20_companies/"):
            new_path = rel.replace("20_companies/", "companies/", 1)
            rename_map.add(RenameEntry(old_path=rel, new_path=new_path, kind="company_note"))
            continue

        # Themes / concepts / people / questions (simple prefix strip)
        for old_prefix, new_prefix, kind in (
            ("10_themes/", "themes/", "theme"),
            ("15_concepts/", "concepts/", "concept"),
            ("25_people/", "people/", "person"),
            ("60_questions/", "questions/", "question"),
        ):
            if rel.startswith(old_prefix):
                new_path = rel.replace(old_prefix, new_prefix, 1)
                rename_map.add(RenameEntry(old_path=rel, new_path=new_path, kind=kind))
                break
        else:
            # 30_theses: resolve ticker from frontmatter, point at companies/TICKER/thesis.md
            if rel.startswith("30_theses/") and rel.endswith(".md"):
                ticker = _read_frontmatter_ticker(path)
                if ticker is None:
                    # Try extracting from handle (often "clmt-...md")
                    stem = Path(rel).stem
                    for k in known_tickers:
                        if stem.lower().startswith(k.lower() + "-") or stem.lower() == k.lower():
                            ticker = k
                            break
                if ticker:
                    new_path = f"companies/{ticker.upper()}/thesis.md"
                    rename_map.add(
                        RenameEntry(
                            old_path=rel,
                            new_path=new_path,
                            kind="thesis_merge",
                            note=f"ticker={ticker} (multi-thesis merge handled by upstream logic)",
                        )
                    )
                else:
                    # Unknown ticker — drop into memos/ as cross-cutting
                    new_path = f"memos/{Path(rel).name}"
                    rename_map.add(
                        RenameEntry(
                            old_path=rel,
                            new_path=new_path,
                            kind="memo",
                            note="thesis without identifiable ticker, moved to top-level memos/",
                        )
                    )
                continue

            # 40_memos
            if rel.startswith("40_memos/") and rel.endswith(".md"):
                stem = Path(rel).stem
                ticker = _memo_ticker_candidate(stem, known_tickers)
                if ticker:
                    new_path = f"companies/{ticker}/memos/{Path(rel).name}"
                    rename_map.add(
                        RenameEntry(
                            old_path=rel,
                            new_path=new_path,
                            kind="memo",
                            note=f"ticker={ticker}",
                        )
                    )
                else:
                    new_path = f"memos/{Path(rel).name}"
                    rename_map.add(
                        RenameEntry(
                            old_path=rel,
                            new_path=new_path,
                            kind="memo",
                            note="cross-cutting (no ticker match)",
                        )
                    )
                continue

            # 80_sources — flatten to _raw/desktop_clips/YYYY-MM-DD/
            if rel.startswith("80_sources/") and rel.endswith(".md"):
                new_path = _source_target_path(rel)
                rename_map.add(RenameEntry(old_path=rel, new_path=new_path, kind="source"))
                continue

            # Unhandled: copy as-is, flag for human review
            rename_map.add(
                RenameEntry(
                    old_path=rel,
                    new_path=rel,  # passthrough
                    kind="copy",
                    note="unhandled — review during dry-run",
                )
            )

    return rename_map


def discover_known_tickers(source_root: Path) -> set[str]:
    """Find ticker folders under 20_companies/."""
    companies_dir = source_root / "20_companies"
    if not companies_dir.is_dir():
        return set()
    tickers: set[str] = set()
    for child in companies_dir.iterdir():
        if child.is_dir():
            tickers.add(child.name.upper())
    return tickers
