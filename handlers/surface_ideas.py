"""Surface ideas handler — non-deterministic analyst engine.

Picks a mode weighted-random each run (recent_signals / question_pursuit /
stale_coverage / theme_deepening / random_exploration), gathers
mode-specific inputs, runs a Haiku or Sonnet call, and feeds the
downstream dedup + autodispatch pipeline shared across all modes.

Operator steering from vault/_analyst/steering.md is prepended to every
prompt so the engine drifts toward whatever the observer is currently
focused on. This is how the atom-splitter keeps busy and how knowledge
compounds — dives generate questions → question_pursuit picks them up →
new dives generate more questions.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import uuid
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

import frontmatter
from sqlalchemy import select, text

from handlers import HandlerContext, HandlerResult
from handlers._common import run_llm
from handlers.prompts.surface_modes import (
    QUESTION_PURSUIT_PROMPT,
    RANDOM_EXPLORATION_PROMPT,
    RECENT_SIGNALS_PROMPT,
    STALE_COVERAGE_PROMPT,
    THEME_DEEPENING_PROMPT,
)
from praxis_core.db.models import SignalFired
from praxis_core.db.session import session_scope
from praxis_core.logging import get_logger
from praxis_core.schemas.payloads import NotifyPayload, SurfaceIdeasPayload
from praxis_core.schemas.surfacing import SurfacedIdea
from praxis_core.schemas.task_types import TaskModel, TaskType
from praxis_core.tasks.capacity import get_pool_capacity
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.time_et import et_iso, now_et, now_utc
from praxis_core.vault.constitution import constitution_prompt_block
from praxis_core.vault.followups import load_open_followups
from praxis_core.vault.section_append import append_to_section
from praxis_core.vault.steering import recent_steering
from praxis_core.vault.writer import atomic_write

log = get_logger("handlers.surface_ideas")

SURFACE_BUDGET_USD = 1.00
MAX_IDEAS_PER_BATCH = 10

# Weighted mode selection. Sum doesn't need to be 100 — random.choices
# normalizes. Tune these over time by watching which modes produce
# ideas that actually convert to shipped memos.
MODE_WEIGHTS: list[tuple[str, int]] = [
    ("recent_signals", 40),
    ("question_pursuit", 25),
    ("stale_coverage", 15),
    ("theme_deepening", 10),
    ("random_exploration", 10),
]


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


def _ticker_universe(vault_root: Path) -> list[str]:
    companies_dir = vault_root / "companies"
    if not companies_dir.exists():
        return []
    return sorted(p.name.upper() for p in companies_dir.iterdir() if p.is_dir())


def _stale_coverage_candidates(
    vault_root: Path, tickers: list[str], *, min_age_days: int = 30, sample: int = 8
) -> list[dict[str, Any]]:
    """Return up to `sample` tickers whose notes.md is stale or nonexistent.
    Includes file age, notes size, and number of dives on disk."""
    cutoff = now_utc() - timedelta(days=min_age_days)
    out: list[dict[str, Any]] = []
    from datetime import datetime

    candidates = random.sample(tickers, k=min(len(tickers), sample * 4))
    for t in candidates:
        notes_path = vault_root / "companies" / t / "notes.md"
        dives_dir = vault_root / "companies" / t / "dives"
        if notes_path.exists():
            try:
                mtime_utc = datetime.fromtimestamp(notes_path.stat().st_mtime, tz=UTC)
                size = notes_path.stat().st_size
            except OSError:
                continue
            if mtime_utc > cutoff and size > 2000:
                continue
            age_days = int((now_utc() - mtime_utc).total_seconds() / 86400)
        else:
            size = 0
            age_days = -1
        dive_count = len(list(dives_dir.glob("*.md"))) if dives_dir.exists() else 0
        out.append(
            {
                "ticker": t,
                "notes_bytes": size,
                "notes_age_days": age_days,
                "dive_count": dive_count,
            }
        )
        if len(out) >= sample:
            break
    return out


def _companies_tagged_with_theme(vault_root: Path, theme_slug: str) -> list[str]:
    tickers: set[str] = set()
    companies_dir = vault_root / "companies"
    if not companies_dir.exists():
        return []
    theme_tag = f"themes/{theme_slug}"
    for d in companies_dir.iterdir():
        if not d.is_dir():
            continue
        notes = d / "notes.md"
        if not notes.exists():
            continue
        try:
            post = frontmatter.load(str(notes))
        except Exception:
            continue
        tags = post.metadata.get("tags") or []
        if any(theme_slug in str(t) or theme_tag in str(t) for t in tags):
            tickers.add(d.name.upper())
    return sorted(tickers)


def _pick_mode(available_modes: set[str] | None = None) -> str:
    pairs = [
        (m, w) for m, w in MODE_WEIGHTS
        if available_modes is None or m in available_modes
    ]
    if not pairs:
        return "recent_signals"
    modes, weights = zip(*pairs, strict=True)
    return random.choices(modes, weights=weights, k=1)[0]


async def _build_recent_signals_run(
    ctx: HandlerContext, steering: str, focus: str, constitution: str
) -> tuple[str, str, TaskModel] | None:
    async with session_scope() as session:
        signals = await _recent_signals(session)
    themes = _active_themes(ctx.vault_root)
    concepts = _concept_titles(ctx.vault_root)
    questions = _open_questions(ctx.vault_root)
    if not signals and not themes:
        return None
    body = _build_llm_input(signals, themes, concepts, questions)
    user = _wrap_user_prompt(
        "recent_signals",
        f"Cross-check the following inputs and emit up to {MAX_IDEAS_PER_BATCH} ranked ideas.\n\n{body}",
        steering,
        focus,
        constitution=constitution,
    )
    return RECENT_SIGNALS_PROMPT, user, TaskModel.SONNET


async def _build_question_pursuit_run(
    ctx: HandlerContext, steering: str, focus: str, constitution: str
) -> tuple[str, str, TaskModel] | None:
    followups = load_open_followups(ctx.vault_root, limit=40)
    if not followups:
        return None
    picked = random.sample(followups, k=min(len(followups), 6))
    lines = [f"# {len(picked)} open followup questions (sampled from {len(followups)})", ""]
    for q in picked:
        lines.append(
            f"## [[questions/{q['slug']}]] — {q['title']}"
        )
        lines.append(f"Ticker: {q.get('ticker') or '—'}  ·  "
                     f"Priority: {q.get('priority')}  ·  "
                     f"Origin: {q.get('origin_task_type') or '—'}  ·  "
                     f"Created: {q.get('created_at','')[:16]}")
        body = q.get("body_excerpt") or ""
        lines.append(body[:500])
        lines.append("")
    user = _wrap_user_prompt(
        "question_pursuit",
        "Triage these open questions and propose 1-3 concrete investigations "
        "that would advance the most fruitful ones.\n\n" + "\n".join(lines),
        steering,
        focus,
        constitution=constitution,
    )
    return QUESTION_PURSUIT_PROMPT, user, TaskModel.SONNET


async def _build_stale_coverage_run(
    ctx: HandlerContext, steering: str, focus: str, constitution: str
) -> tuple[str, str, TaskModel] | None:
    tickers = _ticker_universe(ctx.vault_root)
    if not tickers:
        return None
    cands = _stale_coverage_candidates(ctx.vault_root, tickers, sample=8)
    if not cands:
        return None
    lines = [f"# {len(cands)} stale-coverage candidates"]
    for c in cands:
        age = f"{c['notes_age_days']}d" if c["notes_age_days"] >= 0 else "never"
        lines.append(
            f"- {c['ticker']}: notes {c['notes_bytes']}B (age {age}), "
            f"{c['dive_count']} dives on disk"
        )
    user = _wrap_user_prompt(
        "stale_coverage",
        "Pick 1-3 tickers from the list whose coverage would benefit most "
        "from a refresh dive.\n\n" + "\n".join(lines),
        steering,
        focus,
        constitution=constitution,
    )
    return STALE_COVERAGE_PROMPT, user, TaskModel.HAIKU


async def _build_theme_deepening_run(
    ctx: HandlerContext, steering: str, focus: str, constitution: str
) -> tuple[str, str, TaskModel] | None:
    themes = _active_themes(ctx.vault_root)
    if not themes:
        return None
    theme = random.choice(themes)
    tagged = _companies_tagged_with_theme(ctx.vault_root, theme["slug"])
    theme_path = ctx.vault_root / theme["path"]
    try:
        theme_body = theme_path.read_text(encoding="utf-8")[:2500]
    except OSError:
        theme_body = theme.get("summary_first_200") or ""
    lines = [
        f"# Theme: [[themes/{theme['slug']}]] — {theme['title']}",
        f"Tags: {', '.join(theme.get('tags') or []) or '(none)'}",
        f"Companies tagged with this theme ({len(tagged)}): "
        f"{', '.join(tagged[:25]) or '(none)'}",
        "",
        "## Theme body (first 2500 chars)",
        theme_body,
    ]
    user = _wrap_user_prompt(
        "theme_deepening",
        "Propose 1-3 companies tagged with this theme whose research would "
        "sharpen or reshape the theme.\n\n" + "\n".join(lines),
        steering,
        focus,
        constitution=constitution,
    )
    return THEME_DEEPENING_PROMPT, user, TaskModel.SONNET


async def _build_random_exploration_run(
    ctx: HandlerContext, steering: str, focus: str, constitution: str
) -> tuple[str, str, TaskModel] | None:
    tickers = _ticker_universe(ctx.vault_root)
    if not tickers:
        return None
    picked = random.sample(tickers, k=min(len(tickers), 8))
    lines = [f"# {len(picked)} random universe tickers"]
    for t in picked:
        lines.append(f"- {t}")
    user = _wrap_user_prompt(
        "random_exploration",
        "Pick 0-2 tickers worth a first dive. Empty list is fine if none "
        "are interesting right now.\n\n" + "\n".join(lines),
        steering,
        focus,
        constitution=constitution,
    )
    return RANDOM_EXPLORATION_PROMPT, user, TaskModel.HAIKU


def _wrap_user_prompt(
    mode: str, body: str, steering: str, focus: str, constitution: str = ""
) -> str:
    parts = [f"SURFACE IDEAS (mode: {mode})"]
    if constitution:
        parts.append(constitution)
    if steering:
        parts.append(steering)
    if focus:
        parts.append(f"\nFocus hint from operator (one-shot): {focus}")
    parts.append(body)
    return "\n\n".join(parts)


_MODE_BUILDERS = {
    "recent_signals": _build_recent_signals_run,
    "question_pursuit": _build_question_pursuit_run,
    "stale_coverage": _build_stale_coverage_run,
    "theme_deepening": _build_theme_deepening_run,
    "random_exploration": _build_random_exploration_run,
}


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = SurfaceIdeasPayload.model_validate(ctx.payload)
    vault_root = ctx.vault_root

    steering = recent_steering(vault_root, max_entries=10)
    constitution = constitution_prompt_block(vault_root)
    focus = payload.focus or ""

    chosen = _pick_mode()
    log.info(
        "surface.mode_picked",
        mode=chosen,
        task_id=ctx.task_id,
        constitution_chars=len(constitution),
        steering_chars=len(steering),
    )

    built = await _MODE_BUILDERS[chosen](ctx, steering, focus, constitution)
    if built is None:
        fallback_order = [
            "random_exploration",
            "stale_coverage",
            "recent_signals",
            "question_pursuit",
            "theme_deepening",
        ]
        for m in fallback_order:
            if m == chosen:
                continue
            built = await _MODE_BUILDERS[m](ctx, steering, focus, constitution)
            if built is not None:
                log.info("surface.mode_fallback", original=chosen, used=m)
                chosen = m
                break
    if built is None:
        log.info("surface.no_inputs", task_id=ctx.task_id)
        return HandlerResult(ok=True, message="no inputs across any mode")

    system_prompt, user_prompt, llm_model = built
    result = await run_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=llm_model,
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

    batch_handle = f"batch-{now_et().strftime('%Y%m%d-%H%M')}-{chosen}"
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
        mode=chosen,
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
    """Treat the dispatcher like an atom splitter: keep queueing ideas
    so workers are never idle. Priority encodes importance — HIGH ideas
    get priority=3, MEDIUM get priority=4. Live-path events (8-K/PR
    ingest at priority=0, user-initiated at priority=1, dive fan-out at
    priority=2) all naturally trump surface-dispatched work.

    We queue liberally — queued tasks don't tie up workers, they sit
    until one frees. Only two gates:
      1. Rate-limit state — if Claude is throttled, hold (would fail
         anyway and churn rate_limit_bounces).
      2. Daily cap (AUTODISPATCH_DAILY_CAP=20) to prevent pathological
         runaway if the LLM surfaces many similar ideas.
    """
    if not ideas:
        return

    async with session_scope() as session:
        cap = await get_pool_capacity(session)
        already_today = await _count_autodispatched_today(session)

    # Only hard gate is rate-limit. Capacity-based gating was removed per
    # "treat as atom splitter — keep it running at all times." The
    # priority queue handles ordering when HIGH work arrives.
    if not cap.rate_limit_clear:
        log.info("surface.autodispatch.skip_rate_limit")
        return
    if already_today >= AUTODISPATCH_DAILY_CAP:
        log.info("surface.autodispatch.skip_daily_cap", count=already_today)
        return

    # Every single-or-few-ticker idea is a candidate. Both HIGH and
    # MEDIUM queue — priority encodes the importance, not eligibility.
    candidates: list[tuple[SurfacedIdea, str, int]] = []
    for idea in ideas:
        tickers = [t for t in (idea.tickers or []) if t and t != "UNKNOWN"]
        if not tickers or len(tickers) > 3:
            continue
        # priority=3 HIGH (above scheduler background), =4 MEDIUM.
        # Live 8-K/PR path runs at 0, observer at 1, dive lane at 2.
        prio = 3 if idea.urgency == "high" else 4
        for t in tickers:
            candidates.append((idea, t, prio))

    if not candidates:
        return

    # Respect daily cap only.
    remaining = AUTODISPATCH_DAILY_CAP - already_today
    candidates = candidates[: max(0, remaining)]
    if not candidates:
        return

    # Defer imports to avoid circular / heavy imports at module load
    from praxis_core.db.models import Investigation

    opened = 0
    async with session_scope() as session:
        for idea, ticker, prio in candidates:
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
                priority=prio,
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
                priority=prio,
            )

    log.info(
        "surface.autodispatch.summary",
        opened=opened,
        remaining_daily=remaining,
        pool_utilization=cap.utilization,
    )
