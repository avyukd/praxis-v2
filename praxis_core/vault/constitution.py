"""Analyst constitution — the living, curated rulebook that flows into
every Opus/Sonnet prompt in the system.

Distinct from steering.md (ephemeral one-shot nudges, rolling log).
The constitution is canonical: principles the operator refines over time
and expects to persist. Content lives at vault/_analyst/constitution.md.

Injection points:
- surface_ideas (all modes) — every LLM surface call
- _dive_base (specialist dives) — Opus system prompt
- synthesize_memo — final memo writer
- analyze_filing Sonnet stage — short injection (not Haiku screen)

The read/write helpers below are intentionally dumb — they just shuttle
markdown. The structure (sections, bullets) is the operator's to curate.
"""

from __future__ import annotations

import re
from pathlib import Path

from praxis_core.logging import get_logger
from praxis_core.time_et import et_iso
from praxis_core.vault.writer import atomic_write

log = get_logger("vault.constitution")

_FILE_REL = "_analyst/constitution.md"

_DEFAULT_BODY = """# Analyst constitution

A living rulebook. The operator curates; the analyst reads this on every
Opus/Sonnet prompt and adjusts behavior.

## What to skip
- (add principles here)

## What to favor
- (add principles here)

## Style + conduct
- (add principles here)
"""


def constitution_path(vault_root: Path) -> Path:
    return vault_root / _FILE_REL


def read_constitution(vault_root: Path) -> str:
    """Return the current constitution text, or default scaffold if missing."""
    p = constitution_path(vault_root)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def constitution_prompt_block(vault_root: Path) -> str:
    """Return a ready-to-inject block for system prompts. Empty string if
    the constitution file doesn't exist or has no non-scaffold content.
    Never errors — worst case returns ''."""
    text = read_constitution(vault_root)
    if not text.strip():
        return ""
    # Strip any "(add principles here)" scaffolding so empty sections don't
    # waste tokens.
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        if "(add principles here)" in line:
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines).strip()
    if not cleaned or cleaned.startswith("# Analyst constitution") and len(cleaned) < 120:
        return ""
    return (
        "## Analyst constitution (operator-curated, applies to every "
        "analysis/dive/surface run)\n\n"
        f"{cleaned}\n"
    )


def _ensure_file(vault_root: Path) -> Path:
    p = constitution_path(vault_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        atomic_write(p, _DEFAULT_BODY)
    return p


def append_principle(
    vault_root: Path, rule: str, *, section: str = "What to favor"
) -> Path:
    """Append a bullet to a named section. Creates the section if absent.
    Dedups against an exact-match existing bullet in that section."""
    rule = rule.strip().lstrip("-").strip()
    if not rule:
        raise ValueError("rule is empty")
    p = _ensure_file(vault_root)
    text = p.read_text(encoding="utf-8")

    section_header = f"## {section}"
    if section_header in text:
        new_text = _append_to_section(text, section_header, f"- {rule}")
    else:
        # Section doesn't exist — add it at the end with a timestamp
        new_text = (
            text.rstrip()
            + f"\n\n{section_header}\n- {rule}\n"
        )
    atomic_write(p, new_text)
    log.info("constitution.append", section=section, chars=len(rule))
    return p


def replace_constitution(vault_root: Path, new_markdown: str) -> Path:
    """Full rewrite of the constitution. Backs up the previous version
    to _analyst/constitution.backup-<ts>.md before overwriting."""
    p = constitution_path(vault_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        backup = p.with_name(
            f"constitution.backup-{et_iso().replace(':', '')[:15]}.md"
        )
        try:
            atomic_write(backup, p.read_text(encoding="utf-8"))
        except OSError as e:
            log.warning("constitution.backup_fail", error=str(e))
    atomic_write(p, new_markdown.strip() + "\n")
    log.info("constitution.replace", chars=len(new_markdown))
    return p


def remove_principle(vault_root: Path, substring: str) -> tuple[int, Path]:
    """Remove every bullet line whose text contains `substring`
    (case-insensitive). Returns (removed_count, file_path)."""
    substring = substring.strip()
    if not substring:
        return (0, constitution_path(vault_root))
    p = constitution_path(vault_root)
    if not p.exists():
        return (0, p)
    needle = substring.lower()
    lines = p.read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    removed = 0
    for line in lines:
        if line.lstrip().startswith("-") and needle in line.lower():
            removed += 1
            continue
        kept.append(line)
    if removed:
        atomic_write(p, "\n".join(kept) + "\n")
        log.info("constitution.remove", substring=substring, removed=removed)
    return (removed, p)


_SECTION_RE = re.compile(r"^##\s+", re.MULTILINE)


def _append_to_section(text: str, section_header: str, bullet: str) -> str:
    """Insert `bullet` at the end of `section_header`'s block (before the
    next ## or end-of-file). Dedups exact-match bullets."""
    idx = text.find(section_header)
    if idx < 0:
        return text + f"\n\n{section_header}\n{bullet}\n"
    after = text[idx + len(section_header):]
    next_match = _SECTION_RE.search(after)
    section_end = (idx + len(section_header)) + (
        next_match.start() if next_match else len(after)
    )
    section_body = text[idx + len(section_header):section_end]
    if bullet.strip() in {ln.strip() for ln in section_body.splitlines()}:
        return text  # dedup exact
    insertion = (
        section_body.rstrip("\n")
        + f"\n{bullet}\n"
        + ("\n" if next_match else "")
    )
    return text[: idx + len(section_header)] + insertion + text[section_end:]
