from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import desc, select

from handlers import HandlerContext, HandlerResult
from handlers._common import SYSTEM_PROMPT_PREFIX, read_vault_schema, run_llm
from praxis_core.db.models import Event, Investigation
from praxis_core.db.session import session_scope
from praxis_core.logging import get_logger
from praxis_core.observability.events import emit_event
from praxis_core.schemas.payloads import SynthesizeMemoPayload
from praxis_core.schemas.task_types import TaskModel
from praxis_core.tasks.investigations import touch_investigation
from praxis_core.time_et import now_utc
from praxis_core.vault import conventions as vc
from services.dispatcher.investability import parse_investability

log = get_logger("handlers.synthesize_memo")


SPECIALTIES = [
    "financial-rigorous",
    "business-moat",
    "industry-structure",
    "capital-allocation",
    "geopolitical-risk",
    "macro",
]
FIN_RIGOROUS_MIN_CHARS = 1000


@dataclass
class DiveCoverage:
    financial_path: Path
    financial_chars: int
    financial_investability: str  # CONTINUE | STOP | MALFORMED | MISSING
    financial_stop_reason: str
    override_applied: bool
    override_decision: str  # CONTINUE | STOP | NONE
    present: list[str]  # specialty slugs with non-trivial output


def _collect_dives(vault_root: Path, ticker: str) -> tuple[Path, int, str, str, list[str]]:
    dives_dir = vault_root / "companies" / ticker / "dives"
    fin_path = dives_dir / "financial-rigorous.md"
    fin_chars = 0
    fin_verdict = "MISSING"
    fin_reason = ""
    if fin_path.exists():
        try:
            fin_text = fin_path.read_text(encoding="utf-8", errors="replace")
            fin_chars = len(fin_text)
            verdict, reason = parse_investability(fin_text)
            fin_verdict = verdict
            fin_reason = reason
        except OSError:
            pass

    present: list[str] = []
    for slug in SPECIALTIES:
        p = dives_dir / f"{slug}.md"
        if p.exists():
            try:
                if p.stat().st_size >= 500:
                    present.append(slug)
            except OSError:
                pass
    return (fin_path, fin_chars, fin_verdict, fin_reason, present)


async def _check_override_applied(session, investigation_handle: str) -> tuple[bool, str]:
    if not investigation_handle:
        return (False, "NONE")
    stmt = (
        select(Event)
        .where(Event.event_type == "investability_overridden")
        .where(Event.payload["handle"].astext == investigation_handle)
        .order_by(desc(Event.ts))
        .limit(1)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return (False, "NONE")
    decision = (row.payload or {}).get("decision", "NONE")
    return (True, decision)


async def _gather_coverage(
    session, vault_root: Path, ticker: str, investigation_handle: str
) -> DiveCoverage:
    fin_path, fin_chars, fin_verdict, fin_reason, present = _collect_dives(vault_root, ticker)
    override_applied, override_decision = await _check_override_applied(
        session, investigation_handle
    )
    return DiveCoverage(
        financial_path=fin_path,
        financial_chars=fin_chars,
        financial_investability=fin_verdict,
        financial_stop_reason=fin_reason,
        override_applied=override_applied,
        override_decision=override_decision,
        present=present,
    )


def _memo_quality_sufficient(coverage: DiveCoverage) -> tuple[bool, str]:
    """D27: investigation can be marked 'resolved' only if:
    - financial_rigorous exists + >= FIN_RIGOROUS_MIN_CHARS AND
    - its INVESTABILITY is CONTINUE, or was STOP but has been overridden-CONTINUE AND
    - at least 2 specialists (incl financial_rigorous) on disk.
    """
    if "financial-rigorous" not in coverage.present:
        return (False, "financial-rigorous specialist missing or too short")
    if coverage.financial_chars < FIN_RIGOROUS_MIN_CHARS:
        return (
            False,
            f"financial-rigorous too short ({coverage.financial_chars} chars < {FIN_RIGOROUS_MIN_CHARS})",
        )
    if coverage.financial_investability == "STOP" and not (
        coverage.override_applied and coverage.override_decision == "CONTINUE"
    ):
        return (False, "INVESTABILITY STOP without CONTINUE override")
    if len(coverage.present) < 2:
        return (False, f"only {len(coverage.present)} specialists present, need >=2")
    return (True, f"{len(coverage.present)} specialists ok")


SYSTEM_PROMPT = (
    SYSTEM_PROMPT_PREFIX
    + """
Task: synthesize_memo

You are the coordinator. The specialist dives ran INDEPENDENTLY from each
other — they did not share context, so their conclusions are genuinely
independent reads on the same company. Your job is to cross-check them
and produce a decisive memo with a clear, tradable call.

## Cross-check first, synthesize second

Before writing the memo sections below, read every existing dive under
`companies/<TICKER>/dives/*.md`. Explicitly look for:

- **Corroboration**: where do independent specialists converge? (strong
  signal — this is your conviction base)
- **Disagreement**: where do they diverge? (this is the research gap you
  must resolve — go to primary filings, re-derive)
- **Silence**: what's NOT covered by any specialist? (usually where the
  gap is biggest — explicitly name it)

The `## Dive cross-check` section in the memo must surface all three.

## Required frontmatter (copilot memo.yaml style, embedded in md)

The memo frontmatter MUST include a structured decision + valuation
block. This is what the system reads programmatically — be precise.

```yaml
---
type: memo
ticker: <TICKER>
date: <YYYY-MM-DD>
decision: Buy | Sell | Neutral | Too Hard
current_price: <USD, from mcp__fundamentals__get_price>
fair_value_estimate: <USD, your single-number target>
upside_pct: <((fair_value - current) / current) * 100, rounded int>
entry_range: [low, high]        # price range you'd add/initiate at
exit_range: [low, high]         # price range you'd trim/close at
scores:
  tactical: 1-5                 # near-term setup quality
  fundamental: 1-5              # long-term business quality
data_vintage: <YYYY-MM-DD of most recent filing/data used>
thesis_summary: "<1-2 sentence variant perception>"
links: [<wikilinks to dives / investigations / themes>]
---
```

## Memo body structure (required)

  # <TICKER> Investment Memo — <decision>

  ## Decision & target
  - **Decision:** Buy / Sell / Neutral / Too Hard
  - **Current price:** $X.XX (as of <date>)
  - **Fair value estimate:** $Y.YY
  - **Upside/downside:** ±N% vs current
  - **Entry range:** [$low, $high]
  - **Exit range:** [$low, $high]
  - **Scores:** tactical N/5, fundamental N/5
  - One sentence: why this decision now?

  ## Thesis                (1-2 sentence variant perception)
  ## What's new            (the catalyst that triggered this memo)
  ## Dive cross-check      (explicit agreement/disagreement/silence)
  ## Business overview
  ## Financial analysis    (tables with sourced numbers)
  ## Competitive position
  ## Valuation             (how you got to fair_value_estimate: DCF /
                            comps / NAV / asset-value — show your work,
                            list assumptions, show the math)
  ## Variant perception    (3-col table: Market sees | We see | Why we're right)
  ## Risks                 (kill-criteria-style — specific conditions
                            under which this decision flips)
  ## Confidence & gaps
  ## Related               (wikilinks, bidirectional)

## Valuation discipline — non-negotiable

- **Pull the current price via mcp__fundamentals__get_price(TICKER)**
  before writing the decision block. "Current price" cannot be a
  guess or stale.
- **Fair value must be a single number**, not a range, in the frontmatter.
  Use the range fields (`entry_range` / `exit_range`) for banding.
- **Show your valuation work** in the Valuation section — multiples,
  DCF inputs, comp table, whatever. If you can't show the work, the
  decision is Too Hard, not Neutral.
- **Upside_pct** is a derived field — compute from current + fair_value.
  If you can't set a fair value with conviction, write `null` and set
  decision to "Too Hard."

Decision hygiene: "Too Hard" and "Neutral" are valid. Don't force
conviction. If the dives genuinely disagree after you've gone to primary
sources, "Too Hard" is the honest answer — but "Too Hard" still requires
a target-price attempt + stated reason for the uncertainty.

Memo path: <vault>/companies/<TICKER>/memos/<YYYY-MM-DD>-<memo_handle>.md
"""
)


def _build_coverage_block(coverage: DiveCoverage) -> str:
    lines = ["", "## Dive coverage (context for this synthesis)"]
    lines.append(
        f"- financial-rigorous: {coverage.financial_chars} chars, "
        f"INVESTABILITY={coverage.financial_investability}"
        + (f" (reason: {coverage.financial_stop_reason})" if coverage.financial_stop_reason else "")
    )
    lines.append(f"- specialists present: {', '.join(coverage.present) or '(none)'}")
    if coverage.financial_investability == "STOP" and not (
        coverage.override_applied and coverage.override_decision == "CONTINUE"
    ):
        lines.append("")
        lines.append(
            "## STOP verdict — this memo MUST be a 'Too Hard' memo."
        )
        lines.append(
            "The financial specialist issued an INVESTABILITY: STOP verdict that "
            "has not been overridden by a human. The memo's opening paragraph "
            "must cite the STOP reason below, and the frontmatter `decision:` "
            "field MUST be set to 'Too Hard'."
        )
        lines.append(f"STOP reason: {coverage.financial_stop_reason}")
    elif coverage.override_applied:
        lines.append(
            f"- human override applied: decision={coverage.override_decision} "
            "(proceed with normal memo synthesis)"
        )
    return "\n".join(lines) + "\n"


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = SynthesizeMemoPayload.model_validate(ctx.payload)

    memo_path = vc.company_memo_path(ctx.vault_root, payload.ticker, payload.memo_handle)
    notes_path = vc.company_notes_path(ctx.vault_root, payload.ticker)
    thesis_path = vc.company_thesis_path(ctx.vault_root, payload.ticker)

    # Dives-complete gate (parallel-dive architecture): the 6 specialist
    # dives run concurrently with no resource_key, so without this gate
    # synthesize_memo could grab a worker and read skeletons instead of
    # completed dives. Check that each expected dive file is >1500 bytes
    # (skeleton is ~265 bytes; real dives always >3KB). If any is still
    # skeleton-sized, a sibling task is probably still running — return
    # ok=False with "dives_incomplete" so the worker requeues it.
    dives_dir = ctx.vault_root / "companies" / payload.ticker / "dives"
    skeleton_specialties: list[str] = []
    for slug in SPECIALTIES:
        p = dives_dir / f"{slug}.md"
        if not p.exists():
            continue  # missing is fine — investigation may legitimately skip
        try:
            if p.stat().st_size < 1500:
                skeleton_specialties.append(slug)
        except OSError:
            pass
    if skeleton_specialties:
        log.info(
            "synthesize_memo.dives_incomplete",
            ticker=payload.ticker,
            skeleton=skeleton_specialties,
        )
        return HandlerResult(
            ok=False,
            message=(
                f"dives still writing (skeleton-sized): {skeleton_specialties}; "
                "worker will retry"
            ),
        )

    async def _gather(s) -> DiveCoverage:
        return await _gather_coverage(
            s, ctx.vault_root, payload.ticker, payload.investigation_handle
        )

    if ctx.session is not None:
        coverage = await _gather(ctx.session)
    else:
        async with session_scope() as s:
            coverage = await _gather(s)

    coverage_block = _build_coverage_block(coverage)

    user_prompt = f"""SYNTHESIZE MEMO

Ticker: {payload.ticker}
Investigation: {payload.investigation_handle}
Thesis handle: {payload.thesis_handle or "(none)"}
Memo handle: {payload.memo_handle}

Inputs:
  - Company notes: {notes_path}
  - Company thesis (if exists): {thesis_path}
  - Investigation: <vault>/investigations/{payload.investigation_handle}.md
  - Dive outputs: <vault>/companies/{payload.ticker}/dives/*.md

Write memo at: {memo_path}

Work from the existing notes/thesis/investigation/dive context; do NOT run fresh ingestion.
If the notes are thin, the memo should be short and decisively Neutral or Too Hard.
{coverage_block}
"""

    schema = read_vault_schema(ctx.vault_root)
    system = SYSTEM_PROMPT + ("\n\n## Vault schema\n" + schema if schema else "")

    result = await run_llm(
        system_prompt=system,
        user_prompt=user_prompt,
        model=TaskModel.OPUS,
        max_budget_usd=6.00,
        vault_root=ctx.vault_root,
    )

    quality_ok, quality_reason = _memo_quality_sufficient(coverage)
    new_status = "resolved" if quality_ok else "partial"

    async def _update_investigation(s) -> None:
        inv = (
            await s.execute(
                select(Investigation).where(Investigation.handle == payload.investigation_handle)
            )
        ).scalar_one_or_none()
        if inv and result.finish_reason in ("stop", "max_turns"):
            inv.status = new_status
            if quality_ok:
                inv.resolved_at = now_utc()
            existing = list(inv.artifacts or [])
            rel = str(memo_path.relative_to(ctx.vault_root))
            if rel not in existing:
                existing.append(rel)
            inv.artifacts = existing
            await touch_investigation(s, inv.id)

    if payload.investigation_handle:
        if ctx.session is not None:
            await _update_investigation(ctx.session)
        else:
            async with session_scope() as session:
                await _update_investigation(session)

        await emit_event(
            "handlers.synthesize_memo",
            "memo_synthesized",
            {
                "task_id": ctx.task_id,
                "ticker": payload.ticker,
                "investigation_handle": payload.investigation_handle,
                "investigation_status": new_status,
                "quality_ok": quality_ok,
                "quality_reason": quality_reason,
                "specialists_present": coverage.present,
                "investability": coverage.financial_investability,
                "override_applied": coverage.override_applied,
            },
        )

    log.info(
        "synthesize_memo.done",
        task_id=ctx.task_id,
        finish_reason=result.finish_reason,
        ticker=payload.ticker,
        investigation_status=new_status,
        quality_ok=quality_ok,
    )
    if result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=result, message="rate_limit")
    return HandlerResult(ok=True, llm_result=result)
