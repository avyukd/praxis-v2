"""Surface ideas handler — periodic ideation over recent analyses + vault state (D44-D52)."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

import frontmatter
from sqlalchemy import select, text

from handlers import HandlerContext, HandlerResult
from handlers._common import run_llm
from handlers.prompts.surface_ideas import SYSTEM_PROMPT
from praxis_core.db.models import SignalFired
from praxis_core.db.session import session_scope
from praxis_core.logging import get_logger
from praxis_core.schemas.payloads import NotifyPayload, SurfaceIdeasPayload
from praxis_core.schemas.surfacing import SurfacedIdea
from praxis_core.schemas.task_types import TaskModel, TaskType
from praxis_core.tasks.capacity import get_pool_capacity
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.time_et import et_iso, now_et, now_utc
from praxis_core.vault.section_append import append_to_section
from praxis_core.vault.writer import atomic_write

log = get_logger("handlers.surface_ideas")

SURFACE_BUDGET_USD = 1.00
MAX_IDEAS_PER_BATCH = 10


def _hash_evidence(evidence: list[str]) -> str:
    joined = "\x1f".join(sorted(evidence))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _dedup_handle(idea_type: str, tickers: list[str], themes: list[str]) -> str:
    parts = [idea_type, ",".join(sorted(tickers)), ",".join(sorted(themes))]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _active_themes(vault_root: Path, max_age_days: int = 30) -> list[dict[str, Any]]:
    themes_dir = vault_root / "themes"
    if not themes_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    cutoff = now_utc() - timedelta(days=max_age_days)
    for p in themes_dir.glob("*.md"):
        try:
            mtime_utc = p.stat().st_mtime
        except OSError:
            continue
        from datetime import datetime

        if datetime.fromtimestamp(mtime_utc, tz=UTC) < cutoff:
            continue
        try:
            post = frontmatter.load(str(p))
        except Exception:
            continue
        out.append(
            {
                "slug": p.stem,
                "title": post.metadata.get("title") or p.stem.replace("-", " ").title(),
                "tags": list(post.metadata.get("tags") or []),
                "summary_first_200": (post.content or "")[:200].replace("\n", " "),
                "path": str(p.relative_to(vault_root)),
            }
        )
    return out


def _concept_titles(vault_root: Path) -> list[str]:
    d = vault_root / "concepts"
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.md"))


def _open_questions(vault_root: Path) -> list[str]:
    d = vault_root / "questions"
    if not d.exists():
        return []
    out: list[str] = []
    for p in d.glob("*.md"):
        try:
            post = frontmatter.load(str(p))
        except Exception:
            continue
        if (post.metadata.get("status") or "open").lower() != "resolved":
            out.append(p.stem)
    return out


async def _recent_signals(session, hours: int = 24) -> list[dict[str, Any]]:
    since = now_utc() - timedelta(hours=hours)
    rows = (
        await session.execute(
            select(SignalFired).where(SignalFired.fired_at >= since).order_by(SignalFired.fired_at)
        )
    ).scalars().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        payload = r.payload or {}
        out.append(
            {
                "ticker": r.ticker,
                "signal_type": r.signal_type,
                "urgency": r.urgency,
                "title": payload.get("title", ""),
                "body": (payload.get("body", "") or "")[:200],
                "fired_at": r.fired_at.isoformat() if r.fired_at else None,
            }
        )
    return out


def _build_llm_input(
    signals: list[dict], themes: list[dict], concepts: list[str], questions: list[str]
) -> str:
    lines: list[str] = []
    lines.append(f"# Last 24h signals ({len(signals)})")
    for s in signals[-60:]:
        lines.append(
            f"- {s.get('fired_at','')[:19]} {s.get('ticker') or '?'} "
            f"{s.get('urgency','?')} {s.get('signal_type','?')}: {s.get('title','')}"
        )
    lines.append("")
    lines.append(f"# Active themes (modified last 30d) ({len(themes)})")
    for t in themes[:40]:
        tags_str = ",".join(t.get("tags") or []) or "(no tags)"
        lines.append(f"- [[themes/{t['slug']}]] tags:{tags_str} — {t.get('summary_first_200','')}")
    lines.append("")
    lines.append(f"# Concepts (evergreen) ({len(concepts)})")
    for c in concepts[:60]:
        lines.append(f"- [[concepts/{c}]]")
    lines.append("")
    lines.append(f"# Open questions ({len(questions)})")
    for q in questions[:30]:
        lines.append(f"- [[questions/{q}]]")
    return "\n".join(lines)


def _parse_ideas(raw_text: str) -> list[dict[str, Any]]:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    ideas = data.get("ideas")
    return list(ideas) if isinstance(ideas, list) else []


def _enforce_anomaly_cap(ideas_raw: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Keep at most 1 anomaly. Return (filtered, dropped_count)."""
    out: list[dict[str, Any]] = []
    anomaly_seen = False
    dropped = 0
    for i in ideas_raw:
        if i.get("idea_type") == "anomaly":
            if anomaly_seen:
                dropped += 1
                continue
            anomaly_seen = True
        out.append(i)
    return out, dropped


def _batch_path(vault_root: Path, dt) -> Path:
    return (
        vault_root
        / "_surfaced"
        / dt.strftime("%Y-%m-%d")
        / f"ideas-{dt.strftime('%H%M')}.md"
    )


def _render_batch_md(batch_handle: str, ideas: list[SurfacedIdea]) -> str:
    lines = [f"# Surfaced ideas batch {batch_handle}", "", f"Generated: {et_iso()}", ""]
    if not ideas:
        lines.append("No ideas this batch.")
        return "\n".join(lines) + "\n"
    for i, idea in enumerate(ideas, 1):
        lines.append(
            f"## {i}. [{idea.urgency.upper()}] {idea.idea_type} — "
            f"{', '.join(idea.tickers) or '(no tickers)'}"
        )
        lines.append(f"**Summary:** {idea.summary}")
        lines.append(f"**Rationale:** {idea.rationale}")
        if idea.themes:
            lines.append(f"**Themes:** {', '.join(f'[[themes/{t}]]' for t in idea.themes)}")
        if idea.evidence:
            lines.append("**Evidence:**")
            for e in idea.evidence:
                lines.append(f"- `{e}`")
        lines.append("")
    return "\n".join(lines) + "\n"


async def _persist_and_dedup(
    ideas_raw: list[dict[str, Any]], batch_handle: str
) -> list[SurfacedIdea]:
    """Convert to SurfacedIdea, dedup against last 24h by dedup_handle+evidence_hash."""
    kept: list[SurfacedIdea] = []
    async with session_scope() as session:
        since = now_utc() - timedelta(hours=24)
        for item in ideas_raw[:MAX_IDEAS_PER_BATCH]:
            try:
                tickers = list(item.get("tickers") or [])
                themes = list(item.get("themes") or [])
                evidence = list(item.get("evidence") or [])
                dedup = _dedup_handle(item.get("idea_type", "anomaly"), tickers, themes)
                ev_hash = _hash_evidence(evidence)

                existing = (
                    await session.execute(
                        text(
                            """
                            SELECT evidence_hash FROM surfaced_ideas
                            WHERE dedup_handle = :d AND surfaced_at >= :since
                            ORDER BY surfaced_at DESC LIMIT 1
                            """
                        ),
                        {"d": dedup, "since": since},
                    )
                ).first()
                if existing is not None and existing.evidence_hash == ev_hash:
                    continue  # same evidence seen in last 24h; skip

                handle = f"{item.get('idea_type','anomaly')}-{uuid.uuid4().hex[:8]}"
                idea = SurfacedIdea(
                    handle=handle,
                    dedup_handle=dedup,
                    idea_type=item.get("idea_type", "anomaly"),
                    tickers=tickers,
                    themes=themes,
                    summary=(item.get("summary") or "")[:500],
                    rationale=(item.get("rationale") or "")[:1000],
                    evidence=evidence[:20],
                    urgency=item.get("urgency", "low"),
                    surfaced_at=et_iso(),
                )
                kept.append(idea)

                await session.execute(
                    text(
                        """
                        INSERT INTO surfaced_ideas
                          (handle, dedup_handle, idea_type, tickers, themes,
                           summary, rationale, evidence, evidence_hash,
                           urgency, batch_handle)
                        VALUES
                          (:h, :d, :it, :tk, :th, :s, :r, :ev, :eh, :u, :bh)
                        """
                    ),
                    {
                        "h": handle,
                        "d": dedup,
                        "it": idea.idea_type,
                        "tk": tickers,
                        "th": themes,
                        "s": idea.summary,
                        "r": idea.rationale,
                        "ev": idea.evidence,
                        "eh": ev_hash,
                        "u": idea.urgency,
                        "bh": batch_handle,
                    },
                )
            except Exception as e:
                log.warning("surface.idea_persist_fail", error=str(e))
                continue
    return kept


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = SurfaceIdeasPayload.model_validate(ctx.payload)
    vault_root = ctx.vault_root

    # Gather inputs
    async with session_scope() as session:
        signals = await _recent_signals(session)
    themes = _active_themes(vault_root)
    concepts = _concept_titles(vault_root)
    questions = _open_questions(vault_root)

    if not signals and not themes:
        log.info("surface.no_inputs", task_id=ctx.task_id)
        return HandlerResult(ok=True, message="no inputs to surface")

    user_prompt_body = _build_llm_input(signals, themes, concepts, questions)
    focus_hint = f"\n\nFocus hint from operator: {payload.focus}" if payload.focus else ""
    user_prompt = f"""SURFACE IDEAS

Cross-check the following inputs and emit up to {MAX_IDEAS_PER_BATCH} ranked ideas
per the schema.{focus_hint}

{user_prompt_body}
"""

    result = await run_llm(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=TaskModel.SONNET,
        max_budget_usd=SURFACE_BUDGET_USD,
        vault_root=vault_root,
        allowed_tools=[],
    )
    if result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=result, message="rate_limit")

    ideas_raw = _parse_ideas(result.text)
    ideas_raw, dropped = _enforce_anomaly_cap(ideas_raw)
    if dropped:
        log.info("surface.anomaly_cap_enforced", dropped=dropped, task_id=ctx.task_id)

    batch_handle = f"batch-{now_et().strftime('%Y%m%d-%H%M')}"
    kept = await _persist_and_dedup(ideas_raw, batch_handle)

    # Write batch file
    batch_md_path = _batch_path(vault_root, now_et())
    batch_md_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(batch_md_path, _render_batch_md(batch_handle, kept))

    # Update rolling _surfaced/current.md (simple overwrite with top-20 last-day)
    try:
        rolling_path = vault_root / "_surfaced" / "current.md"
        rolling_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(
            rolling_path,
            _render_batch_md("current-rolling", kept[:20]),
        )
    except Exception as e:
        log.warning("surface.rolling_write_fail", error=str(e))

    # Cross-reference into themes/concepts (D52)
    rel_batch = str(batch_md_path.relative_to(vault_root)).removesuffix(".md")
    for idea in kept:
        for theme_slug in idea.themes:
            theme_path = vault_root / "themes" / f"{theme_slug}.md"
            if not theme_path.exists():
                continue
            bullet = (
                f"{idea.surfaced_at} [[{rel_batch}]] "
                f"**{idea.idea_type}** · tickers: {', '.join(idea.tickers) or '-'} "
                f"— {idea.summary}"
            )
            try:
                append_to_section(
                    theme_path,
                    "## Surfaced ideas",
                    bullet,
                    dedup_substring=f"{batch_handle}:{','.join(idea.tickers)}",
                )
            except Exception as e:
                log.warning("surface.theme_crossref_fail", theme=theme_slug, error=str(e))

    # Fire notify for high-urgency ideas
    high = [i for i in kept if i.urgency == "high"]
    if high:
        async with session_scope() as session:
            for idea in high:
                notify_payload = NotifyPayload(
                    ticker=idea.tickers[0] if idea.tickers else None,
                    signal_type=f"surfaced_{idea.idea_type}",
                    urgency="high",
                    title=f"HIGH {idea.idea_type}: {', '.join(idea.tickers) or '—'}",
                    body=idea.summary,
                    linked_analysis_path=str(batch_md_path.relative_to(vault_root)),
                )
                await enqueue_task(
                    session,
                    task_type=TaskType.NOTIFY,
                    payload=notify_payload.model_dump(),
                    priority=0,
                    dedup_key=f"notify:surface:{idea.handle}",
                )

    # Auto-dispatch investigations for surfaced ideas when the dispatcher
    # has spare capacity (≤80% pool utilization + rate-limit clear). The
    # analyst should always be busy — surface_ideas → orchestrate_dive
    # turns "thinking about patterns" into "actually researching them."
    # Daily cap prevents runaway if the LLM surfaces many similar ideas.
    await _autodispatch_investigations(kept, batch_md_path, vault_root)

    log.info(
        "surface.done",
        task_id=ctx.task_id,
        batch=batch_handle,
        ideas=len(kept),
        high=len(high),
        anomalies_dropped=dropped,
    )
    return HandlerResult(ok=True, llm_result=result, message=f"batch={batch_handle} ideas={len(kept)}")


# Cap auto-dispatched investigations per UTC day so a pathological surface
# run can't flood the queue. Ideas-per-day above this cap fall back to
# ntfy-only behavior.
AUTODISPATCH_DAILY_CAP = 20


async def _count_autodispatched_today(session) -> int:
    today_prefix = now_et().strftime("%Y-%m-%d")
    # dedup_key we use for auto-open is "autoinvest:<date>:..."
    row = await session.execute(
        text(
            "SELECT count(*) FROM tasks "
            "WHERE type='orchestrate_dive' AND dedup_key LIKE :pat "
        ),
        {"pat": f"autoinvest:{today_prefix}:%"},
    )
    return int(row.scalar_one())


async def _autodispatch_investigations(
    ideas: list[SurfacedIdea], batch_md_path: Path, vault_root: Path
) -> None:
    """For each surfaced idea pinned to a clear ticker, open an
    investigation + enqueue orchestrate_dive when the pool has spare
    capacity. HIGH urgency dispatches up to spare_slots; MEDIUM only
    when utilization < 50% (half the pool). Cross-ticker patterns with
    ≤3 tickers fan out one investigation per ticker."""
    if not ideas:
        return

    async with session_scope() as session:
        cap = await get_pool_capacity(session)
        already_today = await _count_autodispatched_today(session)

    if cap.at_capacity:
        log.info(
            "surface.autodispatch.skip_capacity",
            running=cap.running,
            pool_size=cap.pool_size,
            utilization=cap.utilization,
            rl_clear=cap.rate_limit_clear,
        )
        return
    if already_today >= AUTODISPATCH_DAILY_CAP:
        log.info("surface.autodispatch.skip_daily_cap", count=already_today)
        return

    # Pick the set of (idea, ticker) candidates to dispatch this run.
    candidates: list[tuple[SurfacedIdea, str]] = []
    for idea in ideas:
        tickers = [t for t in (idea.tickers or []) if t and t != "UNKNOWN"]
        if not tickers or len(tickers) > 3:
            continue
        if idea.urgency == "high":
            for t in tickers:
                candidates.append((idea, t))
        elif idea.urgency == "medium" and cap.utilization < 0.5:
            # Medium ideas only when the pool is <50% loaded
            for t in tickers:
                candidates.append((idea, t))

    if not candidates:
        return

    # Respect capacity (spare slots for HIGH-urgency live-path buffer)
    # and daily cap.
    remaining = min(cap.spare_slots, AUTODISPATCH_DAILY_CAP - already_today)
    candidates = candidates[: max(0, remaining)]

    if not candidates:
        log.info("surface.autodispatch.no_slots", spare=cap.spare_slots)
        return

    # Defer imports to avoid circular / heavy imports at module load
    from praxis_core.db.models import Investigation

    opened = 0
    async with session_scope() as session:
        for idea, ticker in candidates:
            handle = (
                f"{ticker.lower()}-surfaced-{now_et().strftime('%Y%m%d%H%M')}-{idea.handle[:8]}"
            )
            dedup_key = f"autoinvest:{now_et().strftime('%Y-%m-%d')}:{handle}"
            existing = (
                await session.execute(
                    select(Investigation).where(Investigation.handle == handle)
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            hypothesis = f"Surfaced ({idea.idea_type}, {idea.urgency}): {idea.summary[:240]}"
            inv = Investigation(
                handle=handle,
                status="active",
                scope="company",
                initiated_by="surface_ideas",
                hypothesis=hypothesis,
                entry_nodes=[f"companies/{ticker}"],
                vault_path=f"investigations/{handle}.md",
                research_priority=7 if idea.urgency == "high" else 5,
            )
            session.add(inv)
            await session.flush()
            await enqueue_task(
                session,
                task_type=TaskType.ORCHESTRATE_DIVE,
                payload={
                    "ticker": ticker.upper(),
                    "investigation_handle": handle,
                    "thesis_handle": None,
                    "research_priority": inv.research_priority,
                },
                priority=2,
                dedup_key=dedup_key,
                investigation_id=inv.id,
            )
            opened += 1
            log.info(
                "surface.autodispatch.opened",
                ticker=ticker,
                urgency=idea.urgency,
                idea_type=idea.idea_type,
                handle=handle,
            )

    log.info("surface.autodispatch.summary", opened=opened, capped_at=remaining)
