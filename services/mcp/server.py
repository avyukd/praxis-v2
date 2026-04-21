from __future__ import annotations

import re
import uuid
from datetime import timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy import desc, func, select, update

from praxis_core.config import get_settings
from praxis_core.db.models import (
    DeadLetterTask,
    Heartbeat,
    Investigation,
    SignalFired,
    Source,
    Task,
)
from praxis_core.db.session import session_scope
from praxis_core.llm.rate_limit import RateLimitManager
from praxis_core.logging import configure_logging, get_logger
from praxis_core.observability.events import emit_event
from praxis_core.schemas.task_types import TaskType
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.time_et import et_iso, now_et, now_utc
from praxis_core.vault import conventions as vc
from praxis_core.vault.constitution import (
    append_principle,
    constitution_path,
    read_constitution,
    remove_principle,
    replace_constitution,
)
from praxis_core.vault.memory import Scope, search_vault_memory
from praxis_core.vault.steering import append_steering, recent_steering, steering_path
from praxis_core.vault.writer import atomic_write

log = get_logger("mcp.server")
mcp = FastMCP("praxis-v2")


# -----------------
# Read tools
# -----------------


@mcp.tool()
async def read_company_notes(ticker: str) -> str:
    """Read the compiled notes.md for a ticker. Returns file content or an empty string."""
    settings = get_settings()
    p = vc.company_notes_path(settings.vault_root, ticker)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


@mcp.tool()
async def read_thesis(ticker: str) -> str:
    """Read the thesis.md for a ticker."""
    settings = get_settings()
    p = vc.company_thesis_path(settings.vault_root, ticker)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


@mcp.tool()
async def read_investigation(handle: str) -> str:
    """Read an investigation file by handle."""
    settings = get_settings()
    p = vc.investigation_path(settings.vault_root, handle)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


@mcp.tool()
async def search_vault(
    query: str,
    limit: int = 10,
    scope: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Ranked semantic-lite search over the vault's indexable areas.

    Two-stage: keyword-overlap filter then Haiku rerank with rationales.
    Falls back to stage-1 results on rerank failure / rate-limit.

    `scope` optionally restricts which areas are searched. Valid values:
    "themes", "questions", "concepts", "memos", "sources", "companies".
    Omit for all-areas search.

    Returns ranked hits with path, node_type, title, snippet,
    relevance_score, why_relevant."""
    settings = get_settings()
    # Clamp caller-supplied limit. Too-large values blow out the Haiku
    # rerank prompt and stage-1 scoring budget.
    bounded_limit = max(1, min(int(limit or 10), 50))
    normalized_scope: list[Scope] | None = None
    if scope:
        allowed: set[Scope] = {
            "themes",
            "questions",
            "concepts",
            "memos",
            "sources",
            "companies",
        }
        normalized_scope = [s for s in scope if s in allowed]  # type: ignore[list-item]
    hits = await search_vault_memory(
        settings.vault_root, query, limit=bounded_limit, scope=normalized_scope
    )
    return [h.to_dict() for h in hits]


@mcp.tool()
async def list_recent_analyses(hours: int = 24, limit: int = 50) -> list[dict[str, Any]]:
    """List recent analyze_filing tasks that succeeded."""
    since = now_utc() - timedelta(hours=hours)
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Task.id, Task.payload, Task.finished_at)
                .where(Task.type == TaskType.ANALYZE_FILING.value)
                .where(Task.status == "success")
                .where(Task.finished_at >= since)
                .order_by(desc(Task.finished_at))
                .limit(limit)
            )
        ).all()
    return [
        {
            "task_id": str(r.id),
            "accession": (r.payload or {}).get("accession"),
            "ticker": (r.payload or {}).get("ticker"),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in rows
    ]


@mcp.tool()
async def list_surfaced_ideas(
    hours: int = 24,
    limit: int = 50,
    urgency: str | None = None,
) -> list[dict[str, Any]]:
    """Recent idea-surfacer output from the surfaced_ideas table.

    Each row is one pattern the system flagged — cross-ticker setups,
    thesis conflicts, anomalies, etc. Pair with auto-dispatch:
    surface_ideas auto-opens an investigation for HIGH single-ticker
    ideas, so a row here with urgency=high usually has an
    investigation already running.

    Args:
      hours: look back window (default 24)
      limit: max rows returned
      urgency: filter to one of "high" | "medium" | "low" if set
    """
    from sqlalchemy import text as sql_text

    since = now_utc() - timedelta(hours=hours)
    params: dict[str, Any] = {"since": since, "limit": limit}
    where = ["surfaced_at >= :since"]
    if urgency:
        where.append("urgency = :urgency")
        params["urgency"] = urgency
    q = (
        "SELECT id, handle, idea_type, urgency, summary, tickers, themes, "
        "surfaced_at, notified "
        "FROM surfaced_ideas WHERE " + " AND ".join(where) +
        " ORDER BY surfaced_at DESC LIMIT :limit"
    )
    async with session_scope() as session:
        rows = (await session.execute(sql_text(q), params)).mappings().all()
    return [
        {
            "id": str(r["id"]),
            "handle": r["handle"],
            "idea_type": r["idea_type"],
            "urgency": r["urgency"],
            "tickers": list(r["tickers"] or []),
            "themes": list(r["themes"] or []),
            "summary": r["summary"],
            "surfaced_at": r["surfaced_at"].isoformat() if r["surfaced_at"] else None,
            "notified": bool(r["notified"]),
        }
        for r in rows
    ]


@mcp.tool()
async def show_constitution() -> dict[str, Any]:
    """Return the full analyst constitution — the living rulebook that
    flows into every surface_ideas, specialist dive (Opus), and memo
    prompt. This is what the analyst reads to decide what's worth its
    attention and what to skip. Curate it via append_principle /
    remove_principle / replace_constitution.

    Distinct from steering (ephemeral rolling nudges): the constitution
    is canonical and persists until you edit it."""
    settings = get_settings()
    text = read_constitution(settings.vault_root)
    return {
        "ok": True,
        "path": str(constitution_path(settings.vault_root).relative_to(settings.vault_root)),
        "content": text or "(empty — use append_principle to add rules)",
    }


@mcp.tool()
async def add_principle(
    rule: str, section: str = "What to favor"
) -> dict[str, Any]:
    """Add a principle (rule / bullet) to the analyst constitution.
    The constitution flows into every Opus/Sonnet prompt so refinements
    here ripple across all research.

    Examples:
      add_principle("Skip merger-arb setups with < 5% upside — low
        alpha, not our edge", section="What to skip")
      add_principle("Favor micro-caps $50M-$500M where primary
        research reliably beats consensus", section="What to favor")
      add_principle("Always surface downside symmetrically in every
        memo — no cheerleading", section="Style + conduct")

    Creates the section if it doesn't exist. Exact-duplicate bullets are
    dedup'd. Section names are free-form — typical ones are
    'What to skip', 'What to favor', 'Style + conduct', 'Universe',
    'Process'."""
    settings = get_settings()
    path = append_principle(settings.vault_root, rule, section=section)
    return {
        "ok": True,
        "path": str(path.relative_to(settings.vault_root)),
        "section": section,
    }


@mcp.tool()
async def remove_principle_from_constitution(substring: str) -> dict[str, Any]:
    """Remove every constitution bullet whose text contains `substring`
    (case-insensitive). Returns the number of bullets removed.

    Example:
      remove_principle_from_constitution("merger arb") — drops any rule
        mentioning merger arb."""
    settings = get_settings()
    removed, path = remove_principle(settings.vault_root, substring)
    return {
        "ok": True,
        "removed": removed,
        "path": str(path.relative_to(settings.vault_root)),
    }


@mcp.tool()
async def rewrite_constitution(new_markdown: str) -> dict[str, Any]:
    """Full rewrite of the constitution with `new_markdown` content.
    Backs up the previous version to _analyst/constitution.backup-*.md
    before overwriting. Use when you want to reorganize sections or
    rewrite multiple principles at once; otherwise append_principle /
    remove_principle are safer."""
    settings = get_settings()
    path = replace_constitution(settings.vault_root, new_markdown)
    return {
        "ok": True,
        "path": str(path.relative_to(settings.vault_root)),
        "chars": len(new_markdown),
    }


@mcp.tool()
async def steer_analyst(direction: str) -> dict[str, Any]:
    """Append a natural-language steering note that guides the non-
    deterministic analyst engine (surface_ideas modes). Every surface run
    reads the most recent steering entries and prepends them to the LLM
    prompt, so the analyst drifts toward whatever the observer is
    currently interested in.

    Examples:
      steer_analyst("focus more on micro-caps — we have edge there that
                     we don't in large caps")
      steer_analyst("cool off on biotech for a week, too much
                     duplication in that basket")
      steer_analyst("prioritize balance-sheet fragility threads given
                     the tape this week")

    Returns the steering file path + current entry count."""
    settings = get_settings()
    path = append_steering(settings.vault_root, direction, author="observer")
    return {
        "ok": True,
        "path": str(path.relative_to(settings.vault_root)),
        "chars": len(direction),
    }


@mcp.tool()
async def show_steering() -> dict[str, Any]:
    """Return the current steering file content the analyst sees on each
    surface run — newest first, up to 10 entries."""
    settings = get_settings()
    text = recent_steering(settings.vault_root, max_entries=10)
    return {
        "ok": True,
        "path": str(steering_path(settings.vault_root).relative_to(settings.vault_root)),
        "content": text or "(no steering entries yet)",
    }


@mcp.tool()
async def research_query(
    prompt: str,
    research_priority: int = 5,
) -> dict[str, Any]:
    """Open-ended research entrypoint. Given a freeform prompt, the
    system autonomously decomposes the topic, gathers web sources,
    updates themes/questions/concepts, screens candidate companies,
    kicks off selective ticker deep-dives, and produces a top-level
    cross-cutting memo.

    Examples:
      research_query("research Strait of Hormuz impact on fertilizer
        and best public equity beneficiaries")
      research_query("AI data center power bottlenecks and the best
        public beneficiaries", research_priority=8)
      research_query("uranium enrichment bottlenecks and exposed
        equities", research_priority=7)

    research_priority (0-10) drives budget + depth. Default 5 =
    standard research ($15 whole flow). 7-9 = deeper ($25-50).

    Returns investigation handle + task id. Watch progress via
    list_investigations / list_tasks."""
    from praxis_core.time_et import et_date_str
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "empty prompt"}
    research_priority = max(0, min(10, int(research_priority)))

    # Investigation handle: short slug from prompt + today
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:60] or "research"
    handle = f"research-{slug}-{et_date_str().replace('-', '')}"
    now_stamp = now_et().strftime("%H%M%S")
    handle = f"{handle}-{now_stamp}"[:120]

    async with session_scope() as session:
        tid = await enqueue_task(
            session,
            task_type=TaskType.ORCHESTRATE_RESEARCH,
            payload={
                "prompt": prompt,
                "investigation_handle": handle,
                "research_priority": research_priority,
            },
            priority=1,  # observer-initiated
            dedup_key=f"research:{handle}",
        )
    log.info(
        "mcp.research_query",
        handle=handle,
        priority=research_priority,
        prompt=prompt[:120],
    )
    return {
        "ok": True,
        "investigation_handle": handle,
        "task_id": str(tid) if tid else None,
        "research_priority": research_priority,
    }


@mcp.tool()
async def persist_source(
    url: str,
    title: str,
    body_text: str,
    site: str | None = None,
    publish_date: str | None = None,
    investigation_handle: str | None = None,
    related_nodes: list[str] | None = None,
) -> dict[str, Any]:
    """Persist a web-fetched source into the vault's durable source
    corpus (`_raw/manual/<today>/<slug>.md`). Called by research
    workers (especially gather_sources) to save material sources.

    Dedup by URL hash — calling with the same URL twice returns the
    existing path without rewriting.

    Args:
      url: the source URL (required)
      title: human-readable title (required)
      body_text: the cleaned body text to persist (required)
      site: hostname or publisher name (inferred from URL if omitted)
      publish_date: ISO-ish date string, if known
      investigation_handle: ties this source to a research run
      related_nodes: wikilink targets (e.g. "themes/hormuz",
        "questions/hormuz-fertilizer")
    """
    from praxis_core.vault.sources import persist_web_source

    settings = get_settings()
    path = persist_web_source(
        settings.vault_root,
        url=url,
        title=title,
        body_text=body_text,
        site=site,
        publish_date=publish_date,
        investigation_handle=investigation_handle,
        related_nodes=related_nodes or [],
    )
    if path is None:
        return {"ok": True, "deduped": True, "path": None}
    return {
        "ok": True,
        "deduped": False,
        "path": str(path.relative_to(settings.vault_root)),
    }


@mcp.tool()
async def surface_ideas_now() -> dict[str, Any]:
    """Manually trigger a surface_ideas run outside the normal 15-min
    schedule. Useful after a major news event or when you want to
    force the analyst to re-scan the recent-analyses corpus. Enqueues
    a surface_ideas task at priority 1 (observer-initiated) so it jumps
    ahead of the scheduled runs and auto-dispatched investigations."""
    async with session_scope() as session:
        tid = await enqueue_task(
            session,
            task_type=TaskType.SURFACE_IDEAS,
            payload={"triggered_by": "observer", "manual": True},
            priority=1,
            dedup_key=f"surface_ideas:manual:{now_et().strftime('%Y%m%d%H%M')}",
        )
    return {"ok": True, "task_id": str(tid)}


@mcp.tool()
async def list_fired_signals(hours: int = 24, limit: int = 50) -> list[dict[str, Any]]:
    since = now_utc() - timedelta(hours=hours)
    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(SignalFired)
                    .where(SignalFired.fired_at >= since)
                    .order_by(desc(SignalFired.fired_at))
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": str(r.id),
            "ticker": r.ticker,
            "signal_type": r.signal_type,
            "urgency": r.urgency,
            "fired_at": r.fired_at.isoformat(),
            "title": (r.payload or {}).get("title"),
        }
        for r in rows
    ]


@mcp.tool()
async def list_tasks(
    status: str | None = None,
    task_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    async with session_scope() as session:
        stmt = select(Task).order_by(Task.priority.asc(), Task.created_at.asc()).limit(limit)
        if status:
            stmt = stmt.where(Task.status == status)
        if task_type:
            stmt = stmt.where(Task.type == task_type)
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": str(r.id),
            "type": r.type,
            "status": r.status,
            "priority": r.priority,
            "model": r.model,
            "payload": r.payload,
            "resource_key": r.resource_key,
            "attempts": r.attempts,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "last_error": r.last_error[:200] if r.last_error else None,
        }
        for r in rows
    ]


# -----------------
# Write tools (observer control)
# -----------------


@mcp.tool()
async def cancel_task(task_id: str) -> dict[str, Any]:
    """Cancel a queued or running task."""
    async with session_scope() as session:
        task = await session.get(Task, uuid.UUID(task_id))
        if task is None:
            return {"ok": False, "error": "task not found"}
        if task.status in ("success", "failed", "dead_letter", "canceled"):
            return {"ok": False, "error": f"task already terminal: {task.status}"}
        task.status = "canceled"
        task.finished_at = now_utc()
        task.lease_holder = None
        task.lease_expires_at = None
        await emit_event("mcp.server", "task_cancelled", {"task_id": task_id})
    return {"ok": True, "task_id": task_id}


@mcp.tool()
async def reprioritize(task_id: str, new_priority: int) -> dict[str, Any]:
    if not 0 <= new_priority <= 4:
        return {"ok": False, "error": "priority must be 0-4"}
    async with session_scope() as session:
        task = await session.get(Task, uuid.UUID(task_id))
        if task is None:
            return {"ok": False, "error": "not found"}
        task.priority = new_priority
        await emit_event(
            "mcp.server", "task_reprioritized", {"task_id": task_id, "priority": new_priority}
        )
    return {"ok": True, "task_id": task_id, "new_priority": new_priority}


@mcp.tool()
async def boost_ticker(ticker: str) -> dict[str, Any]:
    """Boost currently queued/running tasks for this ticker by 1 priority tier.

    One-shot — only affects tasks already in-flight at call time. Re-call if you
    want continued emphasis on new tasks spawned after this point.
    """
    async with session_scope() as session:
        stmt = (
            update(Task)
            .where(Task.status.in_(["queued", "partial", "running"]))
            .where(Task.resource_key == f"company:{ticker.upper()}")
            .values(priority=func.greatest(0, Task.priority - 1))
            .returning(Task.id)
        )
        result = await session.execute(stmt)
        affected = len(list(result.scalars().all()))
        await emit_event(
            "mcp.server",
            "boost_ticker",
            {"ticker": ticker, "affected": affected},
        )
    return {"ok": True, "ticker": ticker, "affected": affected}


@mcp.tool()
async def open_investigation(
    ticker: str | None = None,
    theme: str | None = None,
    hypothesis: str | None = None,
    thesis_handle: str | None = None,
) -> dict[str, Any]:
    """Open a new investigation and enqueue the orchestrator.

    Either `ticker` (for company-scoped) or `theme` (for theme-scoped) must be provided.
    """
    if not ticker and not theme:
        return {"ok": False, "error": "provide either ticker or theme"}
    if theme and not ticker:
        return {
            "ok": False,
            "error": (
                "theme investigations are not yet supported for execution; "
                "open a ticker-scoped investigation for now"
            ),
        }

    handle_base = ticker.lower() if ticker else (theme or "").lower().replace(" ", "-")
    handle = f"{handle_base}-{now_et().strftime('%Y%m%d%H%M')}"
    scope = "company" if ticker else "theme"

    async with session_scope() as session:
        inv = Investigation(
            handle=handle,
            status="active",
            scope=scope,
            initiated_by="observer",
            hypothesis=hypothesis,
            entry_nodes=[f"companies/{ticker}" if ticker else f"themes/{theme}"],
            vault_path=f"investigations/{handle}.md",
        )
        session.add(inv)
        await session.flush()

        if ticker:
            await enqueue_task(
                session,
                task_type=TaskType.ORCHESTRATE_DIVE,
                payload={
                    "ticker": ticker.upper(),
                    "investigation_handle": handle,
                    "thesis_handle": thesis_handle,
                },
                priority=1,  # P1 for observer-triggered
                dedup_key=f"orchestrate_dive:{handle}",
                investigation_id=inv.id,
            )

        await emit_event(
            "mcp.server",
            "investigation_opened",
            {"handle": handle, "ticker": ticker, "theme": theme},
        )

    return {"ok": True, "handle": handle, "scope": scope}


# D32 — pause_investigation / resume_investigation removed as of Section C.
# Use cancel_investigation below instead; reintroduce durable hold/unhold
# semantics later with proper dispatch gating if multi-week investigations
# become a real use case.


@mcp.tool()
async def cancel_investigation(
    handle: str, cascade: bool = True
) -> dict[str, Any]:
    """Cancel an investigation and optionally its downstream tasks.

    Default (cascade=True): kills every non-terminal task for this
    investigation. Running tasks observe the cancel via the worker's
    cancel-watch loop within ~5-10s (D31.b) and tear down the subprocess.

    cascade=False: just marks the investigation abandoned; running tasks
    keep going to completion.
    """
    from sqlalchemy import update

    from praxis_core.time_et import now_utc

    async with session_scope() as session:
        inv = (
            await session.execute(select(Investigation).where(Investigation.handle == handle))
        ).scalar_one_or_none()
        if inv is None:
            return {"ok": False, "error": "not found"}

        affected = 0
        if cascade:
            stmt = (
                update(Task)
                .where(Task.investigation_id == inv.id)
                .where(Task.status.in_(("queued", "partial", "running")))
                .values(
                    status="canceled",
                    finished_at=now_utc(),
                    lease_holder=None,
                    lease_expires_at=None,
                    last_error="investigation_canceled",
                )
                .returning(Task.id)
            )
            result = await session.execute(stmt)
            affected = len(list(result.scalars().all()))

        inv.status = "abandoned"
        inv.resolved_at = now_utc()

        await emit_event(
            "mcp.server",
            "investigation_canceled",
            {"handle": handle, "cascade": cascade, "affected_tasks": affected},
        )

    return {
        "ok": True,
        "handle": handle,
        "status": "abandoned",
        "cascade": cascade,
        "affected_tasks": affected,
    }


@mcp.tool()
async def override_investability(
    investigation_handle: str,
    decision: str,
    note: str,
) -> dict[str, Any]:
    """Override the INVESTABILITY gate for an investigation (D20).

    decision='CONTINUE': re-enqueue any sibling dives that were canceled
    by an earlier investability_stop (status='canceled' with last_error
    starting 'investability_stop:'). Resets investigation to active.

    decision='STOP': cancel all queued/running dives for this
    investigation immediately (same semantics as cancel_investigation
    cascade=True, but leaves investigation status 'active' so synthesize_memo
    can still fire a "Too Hard" memo).

    Appends an auditable entry to investigations/<handle>.md and emits
    investability_overridden event.
    """
    from sqlalchemy import update

    from praxis_core.time_et import now_utc

    d = decision.upper().strip()
    if d not in ("CONTINUE", "STOP"):
        return {"ok": False, "error": f"decision must be CONTINUE or STOP, got {decision!r}"}

    async with session_scope() as session:
        inv = (
            await session.execute(
                select(Investigation).where(Investigation.handle == investigation_handle)
            )
        ).scalar_one_or_none()
        if inv is None:
            return {"ok": False, "error": "investigation not found"}

        affected = 0
        if d == "CONTINUE":
            stmt = (
                update(Task)
                .where(Task.investigation_id == inv.id)
                .where(Task.status == "canceled")
                .where(Task.last_error.like("investability_stop:%"))
                .values(
                    status="queued",
                    finished_at=None,
                    last_error=f"investability_overridden_continue: {note[:400]}",
                )
                .returning(Task.id)
            )
            result = await session.execute(stmt)
            affected = len(list(result.scalars().all()))
            inv.status = "active"
        else:  # STOP
            stmt = (
                update(Task)
                .where(Task.investigation_id == inv.id)
                .where(Task.status.in_(("queued", "running")))
                .where(Task.type.like("dive_%"))
                .values(
                    status="canceled",
                    finished_at=now_utc(),
                    lease_holder=None,
                    lease_expires_at=None,
                    last_error=f"investability_overridden_stop: {note[:400]}",
                )
                .returning(Task.id)
            )
            result = await session.execute(stmt)
            affected = len(list(result.scalars().all()))

        settings = get_settings()
        inv_path = vc.investigation_path(settings.vault_root, investigation_handle)
        try:
            existing = inv_path.read_text(encoding="utf-8") if inv_path.exists() else ""
            override_block = (
                f"\n\n## Human override\n\n"
                f"- {et_iso()} — decision: {d} — by: observer\n"
                f"  - note: {note}\n"
                f"  - affected tasks: {affected}\n"
            )
            atomic_write(inv_path, existing + override_block)
        except OSError as e:
            log.warning("override_investability.inv_write_fail", error=str(e))

        await emit_event(
            "mcp.server",
            "investability_overridden",
            {
                "handle": investigation_handle,
                "decision": d,
                "note": note[:500],
                "affected_tasks": affected,
            },
        )

    return {
        "ok": True,
        "handle": investigation_handle,
        "decision": d,
        "affected_tasks": affected,
    }


@mcp.tool()
async def list_investigations(
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List investigations, newest-progress first. Optional status filter:
    one of active | resolved | abandoned. Returns per-investigation task
    counts grouped by status."""
    from sqlalchemy import func

    async with session_scope() as session:
        q = select(Investigation).order_by(desc(Investigation.last_progress_at)).limit(limit)
        if status:
            q = q.where(Investigation.status == status)
        rows = (await session.execute(q)).scalars().all()

        out: list[dict[str, Any]] = []
        for inv in rows:
            # Task counts per status for this investigation
            count_rows = (
                await session.execute(
                    select(Task.status, func.count(Task.id))
                    .where(Task.investigation_id == inv.id)
                    .group_by(Task.status)
                )
            ).all()
            counts = {row[0]: row[1] for row in count_rows}
            out.append(
                {
                    "handle": inv.handle,
                    "status": inv.status,
                    "scope": inv.scope,
                    "initiated_by": inv.initiated_by,
                    "hypothesis": inv.hypothesis,
                    "created_at": inv.created_at.isoformat() if inv.created_at else None,
                    "last_progress_at": (
                        inv.last_progress_at.isoformat() if inv.last_progress_at else None
                    ),
                    "resolved_at": inv.resolved_at.isoformat() if inv.resolved_at else None,
                    "task_counts": counts,
                }
            )
        return out


# -----------------
# Ops tools
# -----------------


@mcp.tool()
async def rate_limit_status() -> dict[str, Any]:
    async with session_scope() as session:
        snap = await RateLimitManager().snapshot(session)
    return {
        "status": snap.status,
        "limited_until_ts": snap.limited_until_ts.isoformat() if snap.limited_until_ts else None,
        "consecutive_hits": snap.consecutive_hits,
        "last_hit_ts": snap.last_hit_ts.isoformat() if snap.last_hit_ts else None,
    }


@mcp.tool()
async def clear_rate_limit() -> dict[str, Any]:
    async with session_scope() as session:
        await RateLimitManager().manual_clear(session)
        await emit_event("mcp.server", "rate_limit_manual_clear", {})
    return {"ok": True}


@mcp.tool()
async def pool_status() -> dict[str, Any]:
    async with session_scope() as session:
        hb = (
            await session.execute(select(Heartbeat).where(Heartbeat.component == "dispatcher.main"))
        ).scalar_one_or_none()
        running = (
            await session.execute(select(func.count(Task.id)).where(Task.status == "running"))
        ).scalar_one()
        queued = (
            await session.execute(
                select(func.count(Task.id)).where(Task.status.in_(["queued", "partial"]))
            )
        ).scalar_one()
    return {
        "running": int(running),
        "queued": int(queued),
        "dispatcher_heartbeat": hb.last_heartbeat.isoformat() if hb else None,
        "dispatcher_status": hb.status if hb else None,
    }


# -----------------
# Vault write (file to vault)
# -----------------


@mcp.tool()
async def file_to_vault(
    path: str, content: str, linked_nodes: list[str] | None = None
) -> dict[str, Any]:
    """Write content to a path within the vault atomically. Use for filing chat results."""
    settings = get_settings()
    vault_root = settings.vault_root.resolve()
    p = settings.vault_root / path
    # Resolve the parent (which must already exist lexically) to catch
    # symlinks pointing outside the vault. The file itself may not
    # exist yet, so we can't resolve(strict=True) on p directly.
    parent_resolved = p.parent.resolve() if p.parent.exists() else (vault_root / p.parent.relative_to(settings.vault_root)).resolve()
    try:
        parent_resolved.relative_to(vault_root)
    except ValueError:
        return {"ok": False, "error": "path escapes vault (symlink or ..)"}
    final = parent_resolved / p.name
    if any(part in {"_raw", "_analyzed"} for part in final.parts):
        return {"ok": False, "error": "cannot write to _raw or _analyzed"}
    atomic_write(final, content)
    await emit_event("mcp.server", "file_to_vault", {"path": path, "linked": linked_nodes or []})
    return {"ok": True, "path": str(final.relative_to(vault_root))}


@mcp.tool()
async def ingest_source(content: str, title: str, source_hint: str | None = None) -> dict[str, Any]:
    """Ingest human-provided content into _inbox_manual/ for manual triage."""
    import hashlib

    settings = get_settings()
    dedup = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    dt = now_et()
    slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", title.lower()).strip("-") or "ingested"
    target = vc.inbox_manual_path(settings.vault_root, dt, f"{slug}-{dedup}")

    async with session_scope() as session:
        stmt = (
            __import__("sqlalchemy.dialects.postgresql", fromlist=["insert"])
            .insert(Source)
            .values(
                dedup_key=f"manual:{dedup}",
                source_type="manual_ingest",
                vault_path=str(target.relative_to(settings.vault_root)),
                extra={"title": title, "source_hint": source_hint, "ingested_at": et_iso(dt)},
            )
            .on_conflict_do_nothing(index_elements=[Source.dedup_key])
        )
        await session.execute(stmt)

    body = (
        "---\n"
        "type: source\n"
        f"source_kind: manual_ingest\n"
        f"title: {title}\n"
        f"source_hint: {source_hint or ''}\n"
        f"ingested_at: {et_iso(dt)}\n"
        "---\n\n" + content
    )
    atomic_write(target, body)

    rel = str(target.relative_to(settings.vault_root))
    async with session_scope() as session:
        await emit_event("mcp.server", "ingest_source", {"path": rel, "title": title})

    return {"ok": True, "path": rel}


# -----------------
# Dead-letter recovery
# -----------------


@mcp.tool()
async def list_dead_letters(limit: int = 50) -> list[dict[str, Any]]:
    """Show the dead-letter queue (tasks that gave up retrying)."""
    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(DeadLetterTask).order_by(desc(DeadLetterTask.failed_at)).limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": str(r.id),
            "failed_at": et_iso(r.failed_at),
            "final_error": r.final_error[:500] if r.final_error else None,
            "task_type": (r.original_task or {}).get("type"),
            "payload": (r.original_task or {}).get("payload"),
            "attempts": (r.original_task or {}).get("attempts"),
        }
        for r in rows
    ]


@mcp.tool()
async def inspect_dead_letter(dead_letter_id: str) -> dict[str, Any]:
    """Full detail for one dead-lettered task."""
    async with session_scope() as session:
        dl = await session.get(DeadLetterTask, uuid.UUID(dead_letter_id))
        if dl is None:
            return {"ok": False, "error": "not found"}
    return {
        "ok": True,
        "id": str(dl.id),
        "failed_at": et_iso(dl.failed_at),
        "final_error": dl.final_error,
        "original_task": dl.original_task,
    }


@mcp.tool()
async def requeue_dead_letter(dead_letter_id: str, reset_attempts: bool = True) -> dict[str, Any]:
    """Put a dead-lettered task back in the queue for another try.

    If reset_attempts=True (default), attempts counter is set to 0 so the task
    gets max_attempts fresh retries. If False, just resets status — typically only
    when you've manually fixed the underlying problem and want one more try.
    """
    from sqlalchemy import text as _text

    async with session_scope() as session:
        dl = await session.get(DeadLetterTask, uuid.UUID(dead_letter_id))
        if dl is None:
            return {"ok": False, "error": "not found"}

        original = dl.original_task or {}
        task_id = original.get("id")
        if not task_id:
            return {"ok": False, "error": "original task has no id field"}

        task = await session.get(Task, uuid.UUID(task_id))
        if task is None:
            return {"ok": False, "error": "task row missing"}

        # Reset to queued state
        await session.execute(
            _text(
                "UPDATE tasks SET status='queued', lease_holder=NULL, lease_expires_at=NULL, "
                "attempts=CASE WHEN :reset_attempts THEN 0 ELSE attempts END, "
                "rate_limit_bounces=0, last_error=NULL, finished_at=NULL "
                "WHERE id=:id"
            ),
            {"id": task.id, "reset_attempts": reset_attempts},
        )
        await session.delete(dl)

        await emit_event(
            "mcp.server",
            "dead_letter_requeued",
            {"task_id": str(task.id), "reset_attempts": reset_attempts},
        )
    return {"ok": True, "task_id": task_id}


def main() -> None:
    configure_logging()
    log.info("mcp.start")
    mcp.run()


if __name__ == "__main__":
    main()
