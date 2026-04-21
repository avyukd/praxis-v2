"""Write followup questions produced by dive self-reflection.

After a specialist dive finishes, a short Haiku call generates 1-3 followup
questions worth revisiting. Those questions land in vault/questions/ with
frontmatter tracking origin (dive type, ticker, investigation handle).

The scheduler + surface_ideas (question_pursuit mode) later pick from this
pool to drive non-deterministic research. This is how knowledge compounds.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from praxis_core.logging import get_logger
from praxis_core.time_et import et_iso
from praxis_core.vault.writer import write_markdown_with_frontmatter

log = get_logger("vault.followups")


def _meta_text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _slugify(title: str, max_len: int = 80) -> str:
    s = re.sub(r"[^a-z0-9\s-]", "", title.lower())
    s = re.sub(r"\s+", "-", s).strip("-")
    return s[:max_len] or "question"


def _dedup_key(title: str, ticker: str | None) -> str:
    base = f"{ticker or ''}::{title.strip().lower()}"
    return hashlib.sha256(base.encode()).hexdigest()[:16]


def write_followup(
    vault_root: Path,
    title: str,
    body: str,
    *,
    origin_task_type: str,
    ticker: str | None = None,
    investigation_handle: str | None = None,
    priority: str = "medium",
    tags: list[str] | None = None,
) -> Path | None:
    """Write a followup question file. Dedup by (title, ticker) hash over the
    last 90d window (checked by scanning existing questions directory).

    Returns the path written, or None if dedup'd or write failed.
    """
    questions_dir = vault_root / "questions"
    questions_dir.mkdir(parents=True, exist_ok=True)

    dedup = _dedup_key(title, ticker)
    slug_base = _slugify(title)
    ticker_prefix = f"{ticker.lower()}-" if ticker else ""
    filename = f"{ticker_prefix}{slug_base}-{dedup[:8]}.md"
    out_path = questions_dir / filename

    if out_path.exists():
        log.info("followup.dedup", title=title[:60], ticker=ticker)
        return None

    metadata: dict = {
        "type": "question",
        "status": "open",
        "priority": priority,
        "origin_task_type": origin_task_type,
        "created_at": et_iso(),
        "dedup_hash": dedup,
        "tags": (tags or []) + ["followup", "auto_generated"],
    }
    if ticker:
        metadata["ticker"] = ticker
        metadata["entry_nodes"] = [f"companies/{ticker}"]
    if investigation_handle:
        metadata["origin_investigation"] = investigation_handle

    md_body = f"# {title}\n\n{body.strip()}\n"
    try:
        write_markdown_with_frontmatter(out_path, body=md_body, metadata=metadata)
        log.info(
            "followup.written",
            path=str(out_path.relative_to(vault_root)),
            ticker=ticker,
            origin=origin_task_type,
        )
        return out_path
    except OSError as e:
        log.warning("followup.write_fail", path=str(out_path), error=str(e))
        return None


def load_open_followups(
    vault_root: Path, limit: int = 50
) -> list[dict]:
    """Load open followup questions for surface_ideas question_pursuit mode.

    Returns list of {slug, title, body_excerpt, ticker, origin_task_type,
    created_at, priority}. Sorted newest first.
    """
    import frontmatter

    questions_dir = vault_root / "questions"
    if not questions_dir.exists():
        return []
    rows: list[dict] = []
    for p in questions_dir.glob("*.md"):
        try:
            post = frontmatter.load(str(p))
        except Exception:
            continue
        meta = post.metadata or {}
        if _meta_text(meta.get("status"), "open").lower() not in ("open", "active"):
            continue
        body = (post.content or "").strip()
        title = body.splitlines()[0].lstrip("# ").strip() if body else p.stem
        rows.append(
            {
                "slug": p.stem,
                "title": title[:200],
                "body_excerpt": body[:800],
                "ticker": meta.get("ticker"),
                "origin_task_type": meta.get("origin_task_type"),
                "origin_investigation": meta.get("origin_investigation"),
                "priority": meta.get("priority") or "medium",
                "created_at": str(meta.get("created_at") or ""),
                "tags": meta.get("tags") or [],
            }
        )
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return rows[:limit]
