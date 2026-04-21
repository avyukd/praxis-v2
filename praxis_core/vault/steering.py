"""Operator steering — rolling notes from the observer that guide the
non-deterministic analyst engine.

The observer's `steer_analyst(text)` MCP tool appends to
`vault/_analyst/steering.md`. Every surface_ideas run reads the last N
entries and prepends them to its LLM user prompt so the analyst naturally
drifts toward whatever Avyuk is currently interested in.

This is a rolling log, not a single-source-of-truth config. Older entries
have less weight (they appear later in the prompt and more fade naturally
as new steering arrives). No deletion needed — time does the work.
"""

from __future__ import annotations

import re
from pathlib import Path

from praxis_core.logging import get_logger
from praxis_core.time_et import et_iso
from praxis_core.vault.writer import atomic_write

log = get_logger("vault.steering")

_FILE_REL = "_analyst/steering.md"
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def steering_path(vault_root: Path) -> Path:
    return vault_root / _FILE_REL


def append_steering(vault_root: Path, text: str, *, author: str = "observer") -> Path:
    """Append a timestamped steering entry. Creates the file if missing."""
    p = steering_path(vault_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    header = f"## {et_iso()} — {author}\n"
    entry = f"{header}\n{text.strip()}\n\n"
    existing = p.read_text(encoding="utf-8") if p.exists() else (
        "# Analyst steering\n\n"
        "Rolling log of operator guidance. The non-deterministic analyst\n"
        "reads the most recent entries before each surface run. Newest first.\n\n"
    )
    atomic_write(p, existing + entry)
    log.info("steering.append", author=author, chars=len(text))
    return p


def recent_steering(vault_root: Path, max_entries: int = 10) -> str:
    """Return the most recent steering entries formatted for prompt inclusion.

    Newest first. Empty string if no file / no entries.
    """
    p = steering_path(vault_root)
    if not p.exists():
        return ""
    try:
        content = p.read_text(encoding="utf-8")
    except OSError:
        return ""
    matches = list(_HEADING_RE.finditer(content))
    if not matches:
        return ""
    entries: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        header = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[start:end].strip()
        if body:
            entries.append((header, body))
    entries.reverse()
    recent = entries[:max_entries]
    lines = ["## Operator steering (most recent first)"]
    for header, body in recent:
        lines.append(f"- **{header}**: {body}")
    return "\n".join(lines)
