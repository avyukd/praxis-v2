"""Append to a named ## section in a markdown file (D52 helper)."""

from __future__ import annotations

import re
from pathlib import Path

from praxis_core.vault.writer import atomic_write


def append_to_section(
    path: Path,
    section_heading: str,
    bullet_line: str,
    *,
    dedup_substring: str | None = None,
) -> bool:
    """Append a bullet under `section_heading` (e.g. '## Surfaced ideas'),
    creating the section if missing. Atomic via rewrite.

    Returns True if appended, False if dedup hit (bullet already present).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    heading = section_heading.strip()
    bullet = f"- {bullet_line.lstrip('- ').rstrip()}\n"

    existing = path.read_text(encoding="utf-8") if path.exists() else ""

    if dedup_substring and dedup_substring in existing:
        return False

    if not existing.strip():
        new = f"{heading}\n\n{bullet}"
        atomic_write(path, new)
        return True

    # Try to find the section heading
    section_re = re.compile(rf"^{re.escape(heading)}\s*$", re.MULTILINE)
    m = section_re.search(existing)
    if m is None:
        # Append a new section at the end
        sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        new = existing + sep + f"{heading}\n\n{bullet}"
        atomic_write(path, new)
        return True

    # Insert bullet at end of the section (before the next ## heading or EOF)
    start = m.end()
    next_match = re.search(r"^##\s", existing[start:], flags=re.MULTILINE)
    if next_match:
        insert_at = start + next_match.start()
    else:
        insert_at = len(existing)

    # Ensure we end the section with a newline before our bullet
    before = existing[:insert_at].rstrip() + "\n"
    after = existing[insert_at:]
    new = before + bullet + ("\n" if after and not after.startswith("\n") else "") + after
    atomic_write(path, new)
    return True
