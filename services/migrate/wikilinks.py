"""Rewrite `[[wikilinks]]` in markdown bodies using a RenameMap.

Handles:
  - `[[target]]` → `[[new_target]]`
  - `[[target|display]]` → `[[new_target|display]]` (display text preserved)
  - `[[target#heading]]` → `[[new_target#heading]]` (heading anchor preserved)
  - `[[target#heading|display]]`
  - Targets that reference paths with or without `.md`.

Targets that don't resolve through the rename map are LEFT ALONE — the validator surfaces
them as broken links so humans can review. We never silently drop a link.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from services.migrate.rename_map import RenameMap

_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")

# Targets that point to intentionally-dropped content. When we encounter a wikilink to one
# of these, we strip the [[...]] brackets and leave the display text (or the target) as
# plain text. This avoids polluting the migrated vault with broken links to content we
# know doesn't exist in v2.
_DEAD_TARGET_PREFIXES: tuple[str, ...] = (
    "00_inbox/",
    "90_meta/",
    "99_development/",
    "50_journal/",
    "70_signals/",
)


def _is_dead_target(target: str) -> bool:
    target = target.strip()
    if target.endswith(".md"):
        target = target[:-3]
    return any(target.startswith(p) for p in _DEAD_TARGET_PREFIXES)


@dataclass
class RewriteResult:
    new_body: str
    rewrote: int
    unresolved: list[str]
    stripped_dead: int = 0


def rewrite_body(body: str, rename_map: RenameMap) -> RewriteResult:
    unresolved: list[str] = []
    rewrote = 0
    stripped = 0

    def _sub(match: re.Match[str]) -> str:
        nonlocal rewrote, stripped
        raw = match.group(1)
        if "|" in raw:
            target_part, display = raw.split("|", 1)
        else:
            target_part, display = raw, None

        if "#" in target_part:
            target, heading = target_part.split("#", 1)
            heading = "#" + heading
        else:
            target, heading = target_part, ""

        # Strip links to intentionally-dropped content
        if _is_dead_target(target):
            stripped += 1
            return display.strip() if display else target.strip()

        new_target = rename_map.lookup(target)
        if new_target is None:
            unresolved.append(target)
            return match.group(0)

        rewrote += 1
        new_inner = new_target + heading
        if display is not None:
            new_inner += "|" + display
        return f"[[{new_inner}]]"

    new_body = _WIKILINK_RE.sub(_sub, body)
    return RewriteResult(
        new_body=new_body, rewrote=rewrote, unresolved=unresolved, stripped_dead=stripped
    )
