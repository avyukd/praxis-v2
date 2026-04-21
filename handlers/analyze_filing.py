"""Two-stage analyze pipeline: Haiku pre-screen → Sonnet analysis.

Handles 8-K filings from EDGAR AND press releases from newswires.
Section A design — see OVERNIGHT.md D1-D14.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from handlers import HandlerContext, HandlerResult
from handlers._common import run_llm
from praxis_core.db.session import session_scope
from praxis_core.filters.market_cap import get_cached_mcap
from praxis_core.logging import get_logger
from praxis_core.schemas.artifacts import AnalysisResult, ScreenResult
from praxis_core.schemas.payloads import AnalyzeFilingPayload
from praxis_core.schemas.task_types import TaskModel, TaskType
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.time_et import et_date_str, et_iso
from praxis_core.vault import conventions as vc
from praxis_core.vault.constitution import constitution_prompt_block
from praxis_core.vault.writer import atomic_write

log = get_logger("handlers.analyze_filing")


TRADE_RELEVANT_MAGNITUDE_THRESHOLD = 0.5

# Truncation caps (Section A D9)
FILING_TRUNCATE_CHARS = 20_000
PR_TRUNCATE_CHARS = 40_000
SCREEN_TRUNCATE_CHARS = 8_000

# Budgets (Section A D10)
HAIKU_SCREEN_BUDGET_USD = 0.10
SONNET_ANALYSIS_BUDGET_USD = 1.50


SCREEN_SYSTEM_PROMPT = """You are a rapid classifier for SEC filings and corporate press releases.

Given the excerpt below, respond with exactly ONE WORD — no punctuation, no
explanation, no formatting — chosen from:

  positive   — disclosure likely to push the stock up
  negative   — disclosure likely to push the stock down
  neutral    — routine, administrative, or unclear impact

Respond with the single word only. Nothing else."""


ANALYSIS_SYSTEM_PROMPT = """You are a senior equity analyst specializing in small-cap and micro-cap stocks.
You are analyzing a single SEC filing or corporate press release.

Your job:
1. Identify what NEW information is disclosed.
2. Assess how MATERIAL it is to the company's cash flows, risk profile, or
   capital structure. Quantify where possible (e.g. "~15% of annual revenue").
3. Classify the likely short-term STOCK REACTION as positive, negative, or
   neutral. This is a prediction of stock behavior given this news alone —
   not an investment recommendation. We save BUY/SELL for later steps with
   broader context.
4. Assign a magnitude from 0.0 (trivial) to 1.0 (transformative).

Classification (stock reaction):
- positive: likely to move the stock up (earnings surprise, accretive M&A,
  major contract, debt refinancing at better terms, FDA approval, significant
  drill/assay results, resource estimate upgrade, etc.)
- negative: likely to move the stock down (earnings miss, impairment,
  auditor change, covenant violation, delisting notice, failed trial,
  going concern, etc.)
- neutral: routine or ambiguous (private placements, option grants, warrant
  extensions, routine corporate updates without material news)

Magnitude:
- 0.0-0.2: minor/routine
- 0.2-0.5: moderate
- 0.5-0.8: significant
- 0.8-1.0: transformative

Output JSON only. No prose, no code fences. Schema:
{
  "classification": "positive"|"negative"|"neutral",
  "magnitude": 0.0-1.0,
  "new_information": "<1-2 sentences — what's actually new>",
  "materiality": "<1-2 sentences — quantified if possible>",
  "explanation": "<1-3 sentences — why this classification + magnitude>"
}"""


def _mcap_str(mcap: int | None) -> str:
    if mcap is None:
        return "unknown"
    if mcap >= 1_000_000_000:
        return f"${mcap / 1_000_000_000:.2f}B"
    if mcap >= 1_000_000:
        return f"${mcap / 1_000_000:.1f}M"
    return f"${mcap:,}"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[TRUNCATED]"


def _read_raw_content(raw_path: Path) -> str:
    """Read the raw filing or press release content from disk."""
    if not raw_path.exists():
        log.warning("analyze.raw_missing", path=str(raw_path))
        return ""
    try:
        return raw_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log.warning("analyze.raw_read_fail", path=str(raw_path), error=str(e))
        return ""


def _parse_screen_response(raw: str) -> str:
    """Extract one of positive|negative|neutral from Haiku's response.

    Fail-open to 'neutral' if unparseable so we don't drop filings on LLM
    formatting quirks (better to spend the Sonnet call than silently lose).
    """
    token = re.sub(r"[^a-zA-Z]", "", raw.strip().lower())
    if "positive" in token:
        return "positive"
    if "negative" in token:
        return "negative"
    if "neutral" in token:
        return "neutral"
    log.warning("analyze.screen_unparseable", raw=raw[:100])
    return "neutral"


def _parse_analysis_json(raw: str) -> dict | None:
    """Extract JSON from Sonnet response. Strips code fences if present."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if len(lines) > 2:
            text = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        log.warning("analyze.json_parse_fail", error=str(e))
        return None


def _analyzed_dir(vault_root: Path, payload: AnalyzeFilingPayload) -> Path:
    if payload.form_type == "press_release":
        if not payload.ticker or not payload.release_id:
            raise ValueError("press_release analysis requires ticker + release_id")
        return vc.analyzed_pr_dir(vault_root, payload.source, payload.ticker, payload.release_id)
    return vc.analyzed_filing_dir(vault_root, payload.form_type, payload.accession)


async def handle(ctx: HandlerContext) -> HandlerResult:
    payload = AnalyzeFilingPayload.model_validate(ctx.payload)

    raw_path = ctx.vault_root / payload.raw_path
    raw_content = _read_raw_content(raw_path)
    if not raw_content:
        return HandlerResult(ok=False, message=f"raw content empty at {raw_path}")

    out_dir = _analyzed_dir(ctx.vault_root, payload)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Lookup cached market cap — no yfinance call from here.
    mcap: int | None = None
    if payload.ticker:
        if ctx.session is not None:
            mcap = await get_cached_mcap(ctx.session, payload.ticker)
        else:
            async with session_scope() as s:
                mcap = await get_cached_mcap(s, payload.ticker)

    item_id = payload.release_id or payload.accession
    screen_excerpt = _truncate(raw_content, SCREEN_TRUNCATE_CHARS)
    screen_user_prompt = f"""Ticker: {payload.ticker or "UNKNOWN"}
Type: {payload.form_type}
Source: {payload.source}
ID: {item_id}
Market cap: {_mcap_str(mcap)}

Content (first {SCREEN_TRUNCATE_CHARS} chars):
---
{screen_excerpt}
---"""

    screen_result = await run_llm(
        system_prompt=SCREEN_SYSTEM_PROMPT,
        user_prompt=screen_user_prompt,
        model=TaskModel.HAIKU,
        max_budget_usd=HAIKU_SCREEN_BUDGET_USD,
        vault_root=ctx.vault_root,
        allowed_tools=[],
    )
    if screen_result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=screen_result, message="rate_limit")

    outcome = _parse_screen_response(screen_result.text)
    screen_out = ScreenResult(
        accession=payload.accession,
        outcome=outcome,  # type: ignore[arg-type]
        screened_at=et_iso(),
        raw_response=screen_result.text[:500],
    )
    atomic_write(out_dir / "screen.json", screen_out.model_dump_json(indent=2))
    log.info(
        "analyze.screen_done",
        task_id=ctx.task_id,
        accession=payload.accession,
        outcome=outcome,
    )

    # Negative screen → done; no Sonnet call.
    # Haiku's one-word label is enough for negatives since we don't
    # trade on them (trade_relevant requires positive|neutral). Running
    # Sonnet for ~$1.50/filing × 25-30 negatives/day is wasted spend.
    # Observers wanting the full pos/neg/neutral distribution should
    # read screen.json (written above), not analysis.json.
    if outcome == "negative":
        return HandlerResult(ok=True, llm_result=screen_result, message=f"screen:{outcome}")
    truncate_limit = (
        PR_TRUNCATE_CHARS if payload.form_type == "press_release" else FILING_TRUNCATE_CHARS
    )
    content_for_analysis = _truncate(raw_content, truncate_limit)
    analysis_user_prompt = f"""Ticker: {payload.ticker or "UNKNOWN"}
Type: {payload.form_type}
Source: {payload.source}
ID: {item_id}
Market cap: {_mcap_str(mcap)}

Content:
---
{content_for_analysis}
---

Respond with valid JSON per the schema."""

    constitution = constitution_prompt_block(ctx.vault_root)
    analysis_system = ANALYSIS_SYSTEM_PROMPT + (
        ("\n\n" + constitution) if constitution else ""
    )
    analysis_result = await run_llm(
        system_prompt=analysis_system,
        user_prompt=analysis_user_prompt,
        model=TaskModel.SONNET,
        max_budget_usd=SONNET_ANALYSIS_BUDGET_USD,
        vault_root=ctx.vault_root,
        allowed_tools=[],
    )
    if analysis_result.finish_reason == "rate_limit":
        return HandlerResult(ok=False, llm_result=analysis_result, message="rate_limit")

    parsed = _parse_analysis_json(analysis_result.text)
    if parsed is None:
        log.warning(
            "analyze.analysis_unparseable",
            task_id=ctx.task_id,
            raw=analysis_result.text[:200],
        )
        return HandlerResult(
            ok=False, llm_result=analysis_result, message="analysis JSON unparseable"
        )

    try:
        result = AnalysisResult(
            accession=payload.accession,
            ticker=payload.ticker,
            form_type=payload.form_type,
            source=payload.source,
            classification=parsed.get("classification", "neutral"),
            magnitude=float(parsed.get("magnitude", 0.0)),
            new_information=parsed.get("new_information", ""),
            materiality=parsed.get("materiality", ""),
            explanation=parsed.get("explanation", ""),
            analyzed_at=et_iso(),
            model="sonnet",
        )
    except Exception as e:
        log.warning("analyze.schema_mismatch", error=str(e), parsed=parsed)
        return HandlerResult(
            ok=False, llm_result=analysis_result, message=f"analysis schema mismatch: {e}"
        )

    atomic_write(out_dir / "analysis.json", result.model_dump_json(indent=2))

    trade_relevant = (
        result.magnitude >= TRADE_RELEVANT_MAGNITUDE_THRESHOLD
        and result.classification in ("positive", "neutral")
    )

    if trade_relevant:
        await _enqueue_downstream(ctx, payload, result, out_dir)

    log.info(
        "analyze.done",
        task_id=ctx.task_id,
        accession=payload.accession,
        classification=result.classification,
        magnitude=result.magnitude,
        trade_relevant=trade_relevant,
    )
    return HandlerResult(ok=True, llm_result=analysis_result)


async def _enqueue_downstream(
    ctx: HandlerContext,
    payload: AnalyzeFilingPayload,
    result: AnalysisResult,
    out_dir: Path,
) -> None:
    """On trade_relevant=True: enqueue notify + orchestrate_dive + compile_to_wiki.

    Dive dedup: one per ticker per ET day (D7). Notify always. Compile always
    when ticker known (D37).
    """
    analysis_rel = str((out_dir / "analysis.json").relative_to(ctx.vault_root))
    item_id = payload.release_id or payload.accession
    urgency = "high" if result.magnitude >= 0.8 else "medium"
    signal_type = f"{payload.form_type}_{result.classification}"
    ticker_display = result.ticker or "?"
    title = f"{result.classification.upper()} {ticker_display} mag={result.magnitude:.2f}"

    async def _do_enqueues(s) -> None:
        # Notify — always on trade_relevant
        await enqueue_task(
            s,
            task_type=TaskType.NOTIFY,
            payload={
                "ticker": result.ticker,
                "signal_type": signal_type,
                "urgency": urgency,
                "title": title,
                "body": result.explanation,
                "linked_analysis_path": analysis_rel,
            },
            priority=0,
            dedup_key=f"notify:{payload.form_type}:{item_id}",
        )

        # Compile to wiki — when ticker known (D37)
        if result.ticker:
            await enqueue_task(
                s,
                task_type=TaskType.COMPILE_TO_WIKI,
                payload={
                    "source_kind": "filing_analysis",
                    "analysis_path": analysis_rel,
                    "ticker": result.ticker,
                    "accession": payload.accession,
                },
                priority=1,
                dedup_key=f"compile:{payload.form_type}:{item_id}",
                resource_key=f"company:{result.ticker}",
            )

        # Orchestrate dive — dedup per ticker per ET day (D7)
        if result.ticker:
            dive_dedup = f"dive:{result.ticker}:{et_date_str()}"
            investigation_handle = f"{result.ticker.lower()}-{et_date_str()}-auto"
            await enqueue_task(
                s,
                task_type=TaskType.ORCHESTRATE_DIVE,
                payload={
                    "ticker": result.ticker,
                    "investigation_handle": investigation_handle,
                    "thesis_handle": None,
                },
                priority=2,
                dedup_key=dive_dedup,
            )

    if ctx.session is not None:
        await _do_enqueues(ctx.session)
    else:
        async with session_scope() as s:
            await _do_enqueues(s)
