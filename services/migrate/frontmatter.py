"""Normalize v1 frontmatter → v2 conventions.

Mostly identity — the v1 vault's conventions were already v2-compatible. Normalization:
  - Adds `migrated_from` and `migrated_at` audit fields.
  - Maps a few status values that differ (`final` → `resolved` for memos).
  - Preserves all other fields, including v1-specific ones like `created_by_focus`,
    `preliminary_decision`, `scores`. These are harmless context for observer Claude.

Does NOT rewrite wikilinks inside frontmatter `links:` arrays — that's left to a post-pass
over the serialized file via the same regex rewriter.
"""

from __future__ import annotations

from typing import Any

import frontmatter

from praxis_core.time_et import et_iso

# status value remapping by doc type
_STATUS_REMAP: dict[str, dict[str, str]] = {
    "memo": {"final": "resolved"},
    # Others: v1 and v2 mostly align (active, answered, paused, done)
}


def normalize_metadata(
    metadata: dict[str, Any], *, source_label: str = "autoresearch"
) -> dict[str, Any]:
    """Return a new metadata dict with v2 normalization applied.

    source_label: "autoresearch" | "copilot_workspace" | "copilot_state"
    """
    new_meta = dict(metadata)

    # Status remapping by doc type
    doc_type = str(new_meta.get("type", "")).lower()
    status = str(new_meta.get("status", "")).lower()
    remap = _STATUS_REMAP.get(doc_type, {})
    if status in remap:
        new_meta["status"] = remap[status]

    # Audit trail
    new_meta["migrated_from"] = source_label
    new_meta["migrated_at"] = et_iso()

    return new_meta


def process_markdown(
    body_with_frontmatter: str,
    *,
    source_label: str = "autoresearch",
) -> tuple[dict[str, Any], str]:
    """Parse, normalize, re-serialize a markdown file.

    Returns (normalized_metadata, body_without_frontmatter). Caller composes the final
    file (usually after wikilink rewriting has been applied to the body separately).

    If the source frontmatter fails to parse as YAML (malformed, syntax errors), we
    preserve the raw source verbatim as body content and add minimal migration metadata,
    so we never lose content due to a single bad file.
    """
    try:
        post = frontmatter.loads(body_with_frontmatter)
        new_meta = normalize_metadata(dict(post.metadata), source_label=source_label)
        return new_meta, post.content
    except Exception as e:
        fallback_meta = normalize_metadata(
            {"migration_fallback_reason": f"frontmatter parse failed: {type(e).__name__}"},
            source_label=source_label,
        )
        return fallback_meta, body_with_frontmatter


def serialize(metadata: dict[str, Any], body: str) -> str:
    """Write frontmatter + body back to a string, stable ordering."""
    post = frontmatter.Post(body, **metadata)
    text = frontmatter.dumps(post)
    if not text.endswith("\n"):
        text += "\n"
    return text
