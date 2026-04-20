# Overnight plan — Monday ship work

Live working doc for the work Claude executes overnight leading into the
2026-04-20 ship. Edit freely; add new sections below existing ones; don't
delete items — strike-through as done.

Current sections:
- [Section A — Ingest + analyze pipeline redesign](#section-a--ingest--analyze-pipeline-redesign)
  (8-K + US PR + CA PR, unified analyze handler with Haiku→Sonnet cascade)
- [Section B — Deep-dive overhaul: copilot parity](#section-b--deep-dive-overhaul--copilot-parity)
  (specialist taxonomy expansion, INVESTABILITY gate, priority tiering,
  wiki-aware coordinator, custom agent spawning, fundamentals MCP, dead code
  cleanup, prompt refactor)
- [Section C — MCP control-plane for running investigations](#section-c--mcp-control-plane-for-running-investigations)
  (cancel_task actually stops work, cancel_investigation one-shot, delete
  the misleading pause/resume tools, list_investigations; foundations for HITL)
- [Section D — compile_to_wiki hardening + idea surfacing MVP](#section-d--compile_to_wiki-hardening--idea-surfacing-mvp)
  (wire compile into the trade-relevant path, add bloat/race/backup
  protections; build surface_ideas task type + cross-cutting pattern
  detection into MVP — explicit override of PLAN.md §16 Loop C deferral)
- [Section E — migration: staging → production with Section A-D fit](#section-e--migration-staging--production-with-section-a-d-fit)
  (audit + restructure existing migrate CLI to match new dive taxonomy,
  new schemas, new vault areas; rewrite MIGRATION.md; add cutover command;
  execute fresh staging run and hand off to production `~/vault`)
- [Section F — Continuous audit/test/iterate loop](#section-f--continuous-audit-test-iterate-loop)
  (/loop cron, every 10 min, that audits the diffs from Sections A-E, runs
  tests, surfaces findings; auto-fix trivial, report the rest to
  `AUDIT_FINDINGS.md`; self-deletes after 3 consecutive empty iterations)
- [Section G — Setup, deployment, and morning observability loop](#section-g--setup-deployment-and-morning-observability-loop)
  (Postgres + secrets + systemd; deploy target decision; IRL smoke;
  pre-market /loop cron from 05:00 ET with tiered self-healing)

**Scope directive from Avyuk (2026-04-19 late eve):** DO NOT CUT SCOPE. Both
Section A AND Section B ship by Monday. Parity with copilot is the bar.

Add new sections below as Avyuk flags more work. Each section should have its
own locked-in decisions, file-by-file change list, open items, and status
checkboxes — following the pattern of Section A.

---

## Section A — Ingest + analyze pipeline redesign

Captures every decision and change agreed with Avyuk during the 2026-04-19
review session. Decisions in this section are **approved for implementation**
— review-as-we-go is the approval mechanism, not a separate signoff gate.
Implementation executes overnight when Avyuk says go.

**Scope (locked in 2026-04-19 evening):** ALL THREE pipelines (US 8-K, US
press releases, Canadian press releases) must be live and working by
2026-04-20 morning. Non-negotiable.

---

## Goal

Three reliable ingest pipelines feeding a single analyze path, producing
strictly-typed analysis artifacts and firing downstream notify + dive tasks:

```
  EDGAR 8-K feed ─┐
                  │
  US GNW PR feeds ┼─► poll ─► filter ─► write raw ─► enqueue analyze_filing
                  │                                           │
  CA newswires ───┘                                           ▼
  (GNW + CNW +                                      Haiku screen → Sonnet
   Newsfile, dedup)                                 full analysis
                                                            │
                                                            ▼
                                                  analysis.json written
                                                            │
                                            trade_relevant? (mag ≥ 0.5 AND
                                            classification ∈ {positive, neutral})
                                                            │
                                                 ┌──────────┴──────────┐
                                                 ▼                     ▼
                                              notify          orchestrate_dive
                                              (ntfy +           (dedup per
                                            signals_fired)     ticker per day)
```

All three ingest sources produce the SAME analyze task shape. Analyze handler
is one implementation serving all three. `compile_to_wiki` is decoupled and
NOT triggered from this path (deferred).

This replaces: (a) the broken analyze_filing that never enqueues downstream,
(b) the missing US PR and CA PR pollers entirely.

---

## Design decisions (locked in)

### D1. Output artifacts per analyzed source
- `analysis.json` — strictly-typed `AnalysisResult`
- `screen.json` — the Haiku pre-screen result, persisted for audit
- No `analysis.md` — prose markdown dropped entirely

### D2. Two-stage LLM cascade
- **Stage 1 — Haiku pre-screen.** One-word response: `positive` / `negative` /
  `neutral` (lowercase). No JSON wrapper — single word, nothing else.
- **Stage 2 — Sonnet full analysis.** Runs iff Haiku returned `positive` OR
  `neutral`. Skipped iff Haiku returned `negative`.
- Sonnet may re-classify to `negative` on deeper read. That's allowed.

### D3. Classification taxonomy
- Labels: `positive` / `negative` / `neutral` (lowercase).
- Framed as **stock reaction** prediction, not investment recommendation.
  Prompt must be explicit.
- `BUY`/`SELL` vocabulary reserved for `synthesize_memo`.

### D4. `AnalysisResult` schema
```python
class AnalysisResult(BaseModel):
    accession: str                    # for 8-K; for PR, use release_id
    ticker: str | None
    form_type: str                    # "8-K" | "press_release"
    source: str                       # "edgar" | "gnw" | "cnw" | "newsfile"
    classification: Literal["positive", "negative", "neutral"]
    magnitude: float = Field(ge=0.0, le=1.0)
    new_information: str
    materiality: str
    explanation: str
    analyzed_at: str                  # ET ISO
    model: str                        # "sonnet" | "haiku"
```

Delete `AnalysisSignals` and `AnalysisThesisImpact` entirely.

### D5. `ScreenResult` schema
```python
class ScreenResult(BaseModel):
    accession: str                    # or release_id for PR
    outcome: Literal["positive", "negative", "neutral"]
    screened_at: str
    raw_response: str                 # Haiku's exact output, for audit
```

### D6. `trade_relevant` derivation (in code)
```python
trade_relevant = (
    result.magnitude >= 0.5
    and result.classification in ("positive", "neutral")
)
```
Threshold: `TRADE_RELEVANT_MAGNITUDE_THRESHOLD = 0.5`, as a Python constant
in `handlers/analyze_filing.py`. NOT an env var.

### D7. Downstream enqueues on `trade_relevant=True`
At end of analyze handler, after `analysis.json` is written:

1. **`notify`** — always fires on trade_relevant.
   - dedup_key: `notify:{form_type}:{accession_or_release_id}`
   - priority: 0
2. **`orchestrate_dive`** — fires on trade_relevant iff ticker is known AND no
   dive already queued/running for this ticker today.
   - dedup_key: `dive:{ticker}:{et_date_str}` — one per ticker per ET day
   - priority: 2
   - investigation_handle: `{ticker}-{et_date}-auto`
   - **No dispatch cap. No per-day quota.** Fires whenever criteria match.
     Human pruning is cost control.
   - If `ticker is None` → skip dive, notify still fires.

### D8. Notify payload derivation
```python
NotifyPayload(
    ticker=result.ticker,
    signal_type=f"{result.form_type}_{result.classification}",  # e.g. "press_release_positive"
    urgency="high" if result.magnitude >= 0.8 else "medium",
    title=f"{result.classification.upper()} {result.ticker or '?'} mag={result.magnitude:.2f}",
    body=result.explanation,
    linked_analysis_path=str(analysis_json_rel_path),
)
```

### D9. Input assembly — inline, no tool loop
Handler reads raw content from disk, inlines in user prompt,
passes `allowed_tools=[]`. No `Read` / `Write` / `Edit` tool use by the LLM.

Truncation:
- **8-K items**: 20,000 chars per item-text blob
- **8-K body (no items)**: 20,000 chars
- **Press release body**: 40,000 chars (PRs are shorter and more free-form)

Financial context block inlined:
- For Monday MVP: market cap only (already in `market_cap_cache`)
- Revenue/income/cash/debt: deferred (see "Deferred" below)

### D10. Budgets
- Haiku screen: `max_budget_usd=0.10` (cap; one-word response)
- Sonnet analysis: `max_budget_usd=1.50` (unchanged — Avyuk's call)

### D11. Validator semantics
A filing/PR screened `negative` by Haiku has only `screen.json` and no
`analysis.json`. Validator treats this as **success** (not partial). Rule:

- If `screen.json` exists and parses and `outcome == "negative"` →
  **success** with just that artifact.
- If `screen.json` exists with `outcome ∈ {positive, neutral}` →
  `analysis.json` must exist and parse as `AnalysisResult`. Otherwise partial.
- If `screen.json` missing → failure.

### D12. `form_type` broadening
Payloads currently declare `form_type: Literal["8-K", "10-Q", "10-K"]`.
Change to plain `str` with a validator function that checks against a
known-good set: `{"8-K", "10-Q", "10-K", "press_release"}`. Easier to extend
later without breaking the Pydantic literal.

### D13. No compile_to_wiki from this loop
Analyze handler does not enqueue compile_to_wiki. Decoupling design TBD.

### D14. Same analyze handler serves all three sources
One `handlers/analyze_filing.py`, branches only on:
- Reading the raw content path (different per source)
- Truncation limits (8-K vs PR have different sane caps)
- The "form_type" label in the output

NOT two separate handlers. The LLM prompts are essentially identical; only the
user-message formatting differs in truncation size.

### D15. Source persistence in Postgres
- **EDGAR state**: handled by dedup on accession in `sources` table (existing).
- **US GNW state**: `system_state` row with key `poller_state.press_us.last_seen`,
  value `{"gnw": {"nyse": "<latest_rid>", "nasdaq": "<latest_rid>"}}` (or
  similar per-feed breakdown).
- **CA state**: `system_state` row with key `poller_state.press_ca.last_seen`,
  value `{"gnw": {...}, "cnw": {...}, "newsfile": {...}}`.

### D16. Canadian market-cap filter
Separate threshold from US — copilot used `CA_MARKET_CAP_THRESHOLD`.
Port as a named Python constant in the CA poller module:
`CA_MARKET_CAP_MAX_USD = 2_000_000_000` (match US for now; adjust later).

yfinance lookup uses `{TICKER}.TO` for TSX and `{TICKER}.V` for TSXV.
Reuse existing `fetch_market_cap_usd()` — it takes any string ticker, so
`"ABC.V"` flows through fine.

### D17. CA dedup logic
Cross-source dedup via ticker + title similarity (SequenceMatcher ≥0.75).
Applied before filter and writing. Order preserved (first occurrence wins).
Port verbatim from copilot.

### D18. HTTP client discipline
Copilot uses sync `requests`. praxis-v2 is async-first. Port to `httpx.AsyncClient`
with a shared rate-bucket (like `edgar_8k.RateBucket`). Add a new `_RATE_NEWSWIRE`
bucket with a relaxed 5 req/s limit (newswires are public; don't hammer).

---

## New dependencies

Add to `pyproject.toml`:
- `beautifulsoup4>=4.12` — HTML parsing for GNW/CNW/Newsfile
- `lxml>=5.0` — parser for bs4 (faster + more forgiving than html.parser)

Already present: `httpx`, `feedparser`, `yfinance`.

Copilot uses `requests` — we don't need it; httpx covers both sync+async.

---

## File-by-file change list

### `praxis_core/schemas/artifacts.py`
- DELETE `AnalysisSignals`
- DELETE `AnalysisThesisImpact`
- ADD `AnalysisResult` per D4
- ADD `ScreenResult` per D5

### `praxis_core/schemas/payloads.py`
- Change `AnalyzeFilingPayload.form_type` from `Literal[...]` to `str`
- Add optional `release_id: str | None = None` field for PR variants
- Add optional `source: str = "edgar"` field (values: edgar, gnw, cnw, newsfile)
- Keep `accession` as-is (for EDGAR); PRs will reuse it = release_id
- Same applies to `TriageFilingPayload` (but not critical this pass — 8-K path
  bypasses triage)

### `praxis_core/vault/conventions.py`
Add new path builders:
```python
def raw_pr_dir(vault: Path, source: str, ticker: str, release_id: str) -> Path:
    return Path(vault) / "_raw" / "press_releases" / source / ticker / release_id

def analyzed_pr_dir(vault: Path, source: str, ticker: str, release_id: str) -> Path:
    return Path(vault) / "_analyzed" / "press_releases" / source / ticker / release_id
```

Both return directories; caller appends `{release.txt, index.json}` for raw
and `{screen.json, analysis.json}` for analyzed.

### `praxis_core/newswire/` — NEW directory (shared library)
Pure parsers + fetchers, no vault or DB side effects beyond HTTP.

- `__init__.py`
- `models.py` — `PressRelease` (release_id, title, url, published_at, source,
  ticker, exchange), `FetchedRelease` (text, metadata dict). Small pydantic
  models.
- `gnw.py` — port of copilot's `newswire/gnw.py`. Adapted:
  - `async def poll_gnw(feed_urls: list[str]) -> list[PressRelease]`
  - `async def fetch_gnw_text(url: str) -> str`
  - Uses httpx AsyncClient + shared rate bucket.
- `cnw.py` — port of copilot's `newswire/cnw.py`. Async.
- `newsfile.py` — port of copilot's `newswire/newsfile.py`. Async.
- `dedup.py` — port of copilot's `newswire/dedup.py`. Pure function.

### `praxis_core/filters/market_cap.py`
Add `async def get_cached_mcap(session, ticker) -> int | None` (cache-only,
no yfinance call — returns None if absent).

For CA: no changes needed. `fetch_market_cap_usd(session, "ABC.V")` works.

### `services/pollers/press_us.py` — NEW
Structure mirrors `services/pollers/edgar_8k.py`:
- `poll_once()` — load state, fetch GNW US feeds (NYSE + NASDAQ), parse,
  filter by mcap, write raw to vault, write `sources` row, enqueue
  `analyze_filing`, update state.
- `run_loop()` — supervisor loop with heartbeat, signal handling, interval.
- `main()` — entrypoint.
- Feeds: `https://www.globenewswire.com/RssFeed/exchange/NYSE` +
  `/exchange/NASDAQ`.
- Filter: mcap ≤ $2B (reuse `passes_mcap_filter`, `keep_unknown=True`).
- Dedup via `sources.dedup_key = f"pr:gnw:{release_id}"`.

### `services/pollers/press_ca.py` — NEW
- Polls three sources: GNW CA (TSX + TSXV feeds) + CNW listing + Newsfile RSS
  per-category.
- Cross-source dedup via `dedup_releases()`.
- Universe filter: mcap ≤ CA_MARKET_CAP_MAX_USD, using `.TO`/`.V` yfinance
  suffix.
- Write to `_raw/press_releases/{source}/{ticker}/{release_id}/`.
- Enqueue `analyze_filing` with `form_type="press_release"`, `source="{gnw|cnw|newsfile}"`.
- State persistence in `system_state` table per source.

### `handlers/analyze_filing.py`
Full rewrite per D1-D14. Branches:
- Read path: 8-K → `_raw/filings/...`; PR → `_raw/press_releases/...`
- Truncation: 8-K → 20K chars; PR → 40K chars
- Output path: 8-K → `_analyzed/filings/...`; PR → `_analyzed/press_releases/...`
- Everything else (Haiku screen → Sonnet → downstream enqueues) is shared.

### `praxis_core/tasks/validators.py`
Rewrite `validate_analyze_filing` per D11. Must handle both output paths
(filings/ and press_releases/).

### `praxis_core/db/session.py` or new helper
Add convenience helpers for `system_state` read/write if needed. Check if
something already exists first — probably worth a small `get_state`/`set_state`
async helper.

### `infra/Procfile`
Add two lines:
```
press_us:  uv run python -m services.pollers.press_us
press_ca:  uv run python -m services.pollers.press_ca
```

### `infra/systemd/`
Add two unit files: `praxis-poller-press-us.service`,
`praxis-poller-press-ca.service`. Copy the 8-K poller unit as template.

### `.env.example`
Add commented note about the new pollers. Do NOT add per-source configuration
as env vars (per env-vars-only-for-runtime-flips preference). Values like
market-cap thresholds and feed URLs live in code.

### `pyproject.toml`
Add `beautifulsoup4>=4.12`, `lxml>=5.0` to `dependencies`.

### Tests (see "Test manifest" below)

---

## Prompt drafts (REVIEW BEFORE IMPLEMENTING)

### Haiku screen — SYSTEM (shared across 8-K and PR)
```
You are a rapid classifier for SEC filings and corporate press releases.

Given the excerpt below, respond with exactly ONE WORD — no punctuation, no
explanation, no formatting — chosen from:

  positive   — disclosure likely to push the stock up
  negative   — disclosure likely to push the stock down
  neutral    — routine, administrative, or unclear impact

Respond with the single word only. Nothing else.
```

### Haiku screen — USER (shared)
```
Ticker: {ticker}
Type: {form_type}  e.g. "8-K" or "press_release"
Source: {source}   e.g. "edgar", "gnw", "cnw", "newsfile"
ID: {accession_or_release_id}
Market cap: {mcap_str}

Content (first {SCREEN_CHARS} chars):
---
{excerpt}
---
```

### Sonnet analysis — SYSTEM (shared)
```
You are a senior equity analyst specializing in small-cap and micro-cap stocks.
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
}
```

### Sonnet analysis — USER (shared)
```
Ticker: {ticker}
Type: {form_type}
Source: {source}
ID: {accession_or_release_id}
Market cap: {mcap_str}

Content:
---
{content_truncated}
---

Respond with valid JSON per the schema.
```

---

## Implementation order (single tonight session)

1. **Dependencies** — add `beautifulsoup4` + `lxml` to pyproject, `uv sync`
2. **Schemas** — `AnalysisResult`, `ScreenResult`, broaden `form_type`
3. **Vault conventions** — `raw_pr_dir`, `analyzed_pr_dir`
4. **`praxis_core/newswire/`** — port all 5 files (models, gnw, cnw, newsfile,
   dedup, fetcher-equivalent)
5. **`get_cached_mcap`** helper
6. **`handlers/analyze_filing.py`** — full rewrite with PR/8-K branches
7. **`services/pollers/press_us.py`** — new poller
8. **`services/pollers/press_ca.py`** — new poller
9. **Validator** — rewrite `validate_analyze_filing`
10. **Procfile** — add new poller entries
11. **Unit tests** — schemas, validators, newswire parsers (use recorded HTML
    fixtures), dedup
12. **Integration test** — handler end-to-end with mocked invoker, 8-K + PR
13. **Manual smoke** — drop one known 8-K, one US PR, one CA PR; watch the
    full flow into analysis.json + downstream enqueues
14. **Commit**

---

## Things NOT in this pass (deferred — flag in FOLLOWUPS)

- **Financial snapshot beyond market cap.** Copilot passed mcap + revenue TTM
  + net income TTM + cash + total debt. Ship with mcap only.
- **Copilot's cost-cutting two-stage screen→full cascade.** We run Sonnet on
  every non-negative. Revisit if Sonnet spend gets uncomfortable.
- **10-Q / 10-K chunking.** Week 2.
- **compile_to_wiki decoupling.** Separate conversation.
- **Human pruning mechanism.** User flagged for later.
- **Env var cleanup.** `EDGAR_FORM_TYPES`, `EDGAR_ITEM_ALLOWLIST`, etc. —
  move to code. Not urgent.
- **Market-cap cache clobber-on-failure bug** in `fetch_market_cap_usd`.
- **Market-hours-aware priority** (P0 vs P1 split).
- **Weighted fair dispatch.**
- **Per-source rate bucket tuning** for newswires. Starting conservative at
  5 req/s shared. Tune later.
- **Newswire state migration.** Fresh start — we'll miss anything published
  before the first poll. Acceptable; copilot's state files exist but aren't
  worth importing for this.

---

## Open items — RESOLVED 2026-04-19 evening

High-level directive from Avyuk: **parity against copilot for tomorrow.** When
in doubt, port copilot's behavior verbatim; tune later.

### O1 — CA sources to ship with → SHIP ALL THREE
GNW CA (TSX + TSXV) + CNW (newswire.ca) + Newsfile (newsfilecorp.com).

### O2 — dedup threshold → 0.75 (copilot default)
`SequenceMatcher.ratio() >= 0.75` for pairs with matching ticker. Catches
reformatted cross-wire duplicates (same release on GNW + CNW + Newsfile)
without collapsing distinct news. Tune later only if we observe false merges.

### O3 — CA market-cap threshold → $2B USD
Same as US. `CA_MARKET_CAP_MAX_USD = 2_000_000_000`. Python constant in
`services/pollers/press_ca.py`.

### O4 — GNW US feeds → NYSE + NASDAQ only
No AMEX / OTC for this pass.

### O5 — Newsfile categories → copilot defaults
`mining-metals`, `technology`, `oil-gas`, `cannabis`, `biotech-pharma`,
`clean-technology`. Python constant in the Newsfile parser module.

---

## Test manifest

Schema:
- `tests/unit/test_schemas.py` — `AnalysisResult` happy path + bounds;
  `ScreenResult`.

Validators:
- `tests/unit/test_validators.py` — matrix:
  - Haiku negative (only screen.json) → success
  - Haiku positive + Sonnet success → success
  - Haiku positive + Sonnet missing analysis.json → partial
  - Haiku positive + Sonnet malformed analysis.json → partial
  - Haiku missing screen.json → failure
  - Both filings/ and press_releases/ paths

Newswire parsers:
- `tests/unit/test_newswire_gnw.py` — RSS fixture with known structure,
  verify extracted PressRelease records.
- `tests/unit/test_newswire_cnw.py` — HTML listing fixture.
- `tests/unit/test_newswire_newsfile.py` — RSS fixture.
- `tests/unit/test_newswire_dedup.py` — dedup matrix.

Pollers (light, since they do HTTP):
- `tests/unit/test_press_us_poller.py` — mock httpx, verify filter + write flow.
- `tests/unit/test_press_ca_poller.py` — mock httpx, verify dedup +
  universe filter + write flow.

Handler (integration-lite, in `tests/integration/`):
- `test_analyze_filing_handler_8k.py` — scenario matrix with mocked invoker
- `test_analyze_filing_handler_pr.py` — scenario matrix with mocked invoker

Existing tests must not regress:
- All currently-passing tests in `tests/unit/` and `tests/integration/`.

---

## Status

- [x] D1-D18 decisions — locked in through review session 2026-04-19
- [x] Prompt drafts reviewed
- [x] Open items O1-O5 resolved (parity-with-copilot)
- [x] Dependencies added (beautifulsoup4, lxml, boto3, pyyaml, psycopg2-binary, moto)
- [x] Schemas written (AnalysisResult, ScreenResult; AnalysisSignals deleted)
- [x] Vault conventions extended (raw_pr_dir, analyzed_pr_dir)
- [x] praxis_core/newswire/ ported (models, gnw, cnw, newsfile, dedup, rate, state)
- [x] get_cached_mcap helper written
- [x] Handler rewritten (two-stage Haiku→Sonnet cascade + downstream enqueues)
- [x] press_us poller written
- [x] press_ca poller written
- [x] Validator updated (screen.json required; analysis.json gated on outcome)
- [x] Procfile + systemd units added (infra/systemd/*.service live)
- [x] Unit tests green (202/202 pass, 23 skipped)
- [x] Manual smoke verified via live pipeline (200+ analyze successes today)
- [x] Commit (multiple — section A through press fixes)

---

## Section B — Deep-dive overhaul — copilot parity

Captured 2026-04-19 late evening. Decisions approved for implementation via
review-as-we-go. This section runs alongside Section A overnight —
**no scope cuts, per explicit user direction.**

**Scope:** Bring praxis-v2's deep-dive pipeline to parity with praxis-copilot's
research pipeline. Richer specialist taxonomy, battle-tested prompts ported
verbatim, human-overridable INVESTABILITY gate, priority-tiered research
budget, wiki-aware coordinator that skips redundant dimensions, ability to
enqueue 1-2 custom specialists per investigation, working fundamentals MCP
(yfinance-backed, tested), dead code removed, every handler's system prompt
clearly visible and reviewable.

---

### Design decisions (locked in)

### D19. Specialist taxonomy — port copilot's 6 + custom slot
Replace current 3 (`dive_business`, `dive_moat`, `dive_financials`) with:

| Task type | Always runs? | Notes |
|---|---|---|
| `dive_financial_rigorous` | **YES, first** | Rename of `dive_financials`. Emits INVESTABILITY: CONTINUE/STOP line at end of output. |
| `dive_business_moat` | Coordinator decides | Merge of current `dive_business` + `dive_moat`. Copilot has them as one specialist; match that. |
| `dive_industry_structure` | Coordinator decides | New. Industry economics, cycle position, structural trends. |
| `dive_capital_allocation` | Coordinator decides | New. M&A discipline, SBC/dilution, buyback policy, exec comp. |
| `dive_geopolitical_risk` | Coordinator decides (often SKIP) | New but often skipped per Avyuk. Run only for international exposure / sanctions risk / regulatory-political exposure names. |
| `dive_macro` | Coordinator decides (often SKIP) | New but often redundant per Avyuk. Skip if wiki already has a rich current theme covering the relevant macro exposure. |
| `dive_custom` | On-demand (max 2 per investigation) | New. Orchestrator can enqueue up to 2 ad-hoc specialists with custom `specialty` + `custom_system_prompt` payload fields. Used for idiosyncratic angles (e.g., "uranium market specialist for UUUU", "coal restructuring specialist for HNRG"). |

Delete the old `dive_business` and `dive_moat` task types cleanly. Rename
`dive_financials` → `dive_financial_rigorous`. Update `TaskType` enum,
`MODEL_TIERS`, `TASK_RESOURCE_KEYS`, `VALIDATORS`, and every enqueue site.

### D20. INVESTABILITY gate — auditable and human-overridable
`dive_financial_rigorous` system prompt requires the handler's output to end
with a single line:

```
INVESTABILITY: CONTINUE — <one sentence reason>
```

or

```
INVESTABILITY: STOP — <one sentence reason>
```

**Worker post-dive logic** (in `services/dispatcher/worker.py`, after
validation succeeds for `dive_financial_rigorous`):
1. Read the output file, extract the INVESTABILITY line via regex.
2. If `STOP`:
   - Emit `event_type="investability_stop"` with the reason, ticker,
     investigation_handle, dive task_id.
   - Cancel sibling dives in this investigation that haven't started
     (status=queued, same investigation_id, different task type). Mark them
     `canceled` with `last_error="investability_stop: <reason>"`.
   - Still allow `synthesize_memo` to run — it will produce a terse "Too Hard"
     memo explaining the stop.
3. If `CONTINUE`: no-op, specialists proceed.
4. If line missing or malformed: treat as `CONTINUE` (fail-open) and emit a
   `event_type="investability_malformed"` event for audit.

**Human override mechanism (new MCP tool):**

```python
# services/mcp/server.py
@mcp.tool()
async def override_investability(
    investigation_handle: str,
    decision: Literal["CONTINUE", "STOP"],
    note: str,
) -> dict:
    """Override the INVESTABILITY gate for an investigation.

    CONTINUE: re-enqueue all canceled sibling dives (and synthesize_memo if
    it was skipped). Resets the investigation's status to active.

    STOP: cancel all queued/running dives for this investigation immediately.
    """
```

Override writes:
- `event_type="investability_overridden"` to `events`
- Appends to `investigations/<handle>.md` with a `## Human override` section
  carrying timestamp + decision + note + who (stub "observer" for now).
- Resets `canceled` sibling tasks back to `queued` (if CONTINUE) or cancels
  running ones (if STOP).

Auditability: every INVESTABILITY decision, human or machine, is an event in
the `events` table with full context. Dashboard will surface these (follow-up
work for Section C if needed; for Monday, SQL-queryable is enough).

### D21. Priority tiering — port `ResearchBudget`
Port `src/cli/research_prompt.py::ResearchBudget.from_priority` verbatim into
`praxis_core/research/budget.py`. Keep the 5 tiers (quick screen / standard
scan / standard research / deep research / full deep dive) and their per-tier
word/web-lookup caps.

**Priority storage:**
- Add `research_priority: int` (default 5, range 0-10) to the
  `investigations` table via Alembic migration.
- Add `research_priority: int = 5` to `OrchestrateDivePayload`.
- Default for auto-triggered dives (from `analyze_filing → trade_relevant`):
  priority = 5 (standard research). Can be overridden by observer.
- Observer's `open_investigation` MCP tool gains a `research_priority` kwarg.

**Handler usage:**
Each dive handler reads `research_priority` from the investigation row at
start, derives a `ResearchBudget`, and injects the word limit / lookup cap
into its own user prompt (per copilot's `{budget.specialist_words:,} words`
pattern).

### D22. Coordinator behavior lives in `orchestrate_dive`
Replace the current thin `orchestrate_dive` handler with a Sonnet-driven
coordinator pass that does:

1. **Wiki crawl.** Read the vault to understand what's already known:
   - `companies/<TICKER>/notes.md` (if exists)
   - `companies/<TICKER>/thesis.md` (if exists)
   - Recent `memos/` files tagged with this ticker or related themes
   - `themes/` directory — scan for themes that overlap the ticker's likely
     exposures (keyword match + recent-modification filter; if a theme file
     is <30 days old and its tags include anything plausibly relevant, flag
     it as "covered")
   - `concepts/` — same logic but looser (concepts are evergreen)

2. **Emit a coverage assessment to the investigation file.** Under a new
   `## Existing coverage` section: list themes/concepts already covering
   dimensions this dive would otherwise touch. Example: if the ticker is
   Iran-exposed and `themes/strait-of-hormuz.md` exists and is recent, note
   "geopolitical angle already covered in [[themes/strait-of-hormuz]]".

3. **Plan specialist set.** Sonnet emits a plan under `## Plan` that:
   - ALWAYS includes `dive_financial_rigorous` first
   - Optionally includes `dive_business_moat`, `dive_industry_structure`,
     `dive_capital_allocation` per relevance
   - SKIPS `dive_geopolitical_risk` unless there's no existing coverage AND
     the company has meaningful exposure
   - SKIPS `dive_macro` unless there's no existing coverage AND the company
     has meaningful macro sensitivity
   - May add up to 2 `dive_custom` entries with a clearly named specialty
     (e.g. `"uranium-market-specialist"`) and a paragraph describing what
     that specialist should focus on
   - Each plan line includes a one-sentence `why: <reason>` for audit

4. **Plan parser extends to handle `dive_custom`.** When the orchestrator's
   plan has a line like:
   ```
   3. dive_custom specialty=uranium-market-specialist
      why: UUUU's value hinges on uranium spot pricing and contract-mix shifts
      focus: Cameco Q earnings implications, Sprott Physical flows, spot vs term spread
   ```
   the parser extracts `specialty`, `why`, `focus` and enqueues a `dive_custom`
   task with those fields in the payload.

5. **Re-planning window (bounded).** One-shot is the default — once
   orchestrate_dive writes its plan and enqueues tasks, that's the plan. BUT:
   introduce a limited mechanism where `dive_financial_rigorous` can append
   a `## Replan request` section to the investigation file if it surfaces
   something that demands a new specialist. A post-dive step reads this and
   re-invokes orchestrate_dive with `mode="replan"` that can append up to 1
   additional `dive_custom` to the plan (respecting the 2-custom cap total).
   This covers the "financials reveal we should also do dive_management" case
   without requiring full replay.

### D23. `dive_custom` handler
New file `handlers/dive_custom.py`. Payload:

```python
class DiveCustomPayload(BaseModel):
    ticker: str
    investigation_handle: str
    specialty: str           # short slug, e.g. "uranium-market-specialist"
    why: str                 # why this specialist was spawned
    focus: str               # what to focus on — written by orchestrator
```

Handler constructs a system prompt from a template that embeds `specialty`
and `focus` verbatim. It's essentially a "meta" specialist — the prompt is
partially LLM-generated. Output file:
`companies/<TICKER>/dives/<specialty>.md`.

Validator checks file exists and has a minimum length (≥500 chars to reject
near-empty outputs).

### D24. Wiki-aware coverage check — heuristic, not semantic
For Monday: keyword-match + recent-modification. Precise:

```python
# praxis_core/vault/coverage.py
def find_existing_coverage(
    vault_root: Path,
    ticker: str,
    dimensions: list[Literal["geopolitical","macro","industry","moat","financial","capital_allocation"]],
    *,
    freshness_days: int = 30,
) -> dict[str, list[Path]]:
    """Return dimension -> list of wiki paths that plausibly already cover it.

    Scans themes/ and concepts/, matches frontmatter tags + title tokens
    against dimension-specific keyword sets. Only includes files modified
    within freshness_days (themes decay; concepts don't, so concepts ignore
    the window).
    """
```

Keyword sets hardcoded per dimension (e.g., geopolitical → `{sanctions,
tariff, war, sovereign, regulatory, regime, iran, russia, china-exposure,
export-control}`). Good enough for Monday. Semantic search via pgvector is
a later upgrade and is already flagged deferred in PLAN §16.

### D25. Fundamentals MCP — yfinance-backed, tested
New MCP server at `services/mcp/fundamentals/server.py` exposing:

```python
company_overview(ticker) -> dict
list_financial_metrics(statement) -> list[str]   # statement ∈ {income,balance,cashflow}
get_financial_data(statement, metrics, period_type, count) -> dict
get_full_statement(statement, period_type, count) -> dict
get_earnings(ticker, count) -> list
get_holders(ticker) -> dict
get_price(ticker) -> dict                        # current/delayed
search_fundamentals(keyword) -> list[str]
```

All backed by `yfinance.Ticker(ticker).info/financials/earnings/...`.
Cached in a new `fundamentals_cache` Postgres table keyed by
`(ticker, method, params_hash)` with 1h TTL.

Wired into the worker via `vault/.mcp-config.json` template update so dive
handlers automatically get these tools. Update `allowed_tools` in
`handlers/_common.py::run_llm` (or add a `dive_specific_tools` list) to
include the MCP tool names:

```python
DIVE_ALLOWED_TOOLS = [
    "Read", "Write", "Edit", "Glob", "Grep", "Bash(mkdir:*)",
    "mcp__fundamentals__company_overview",
    "mcp__fundamentals__list_financial_metrics",
    "mcp__fundamentals__get_financial_data",
    "mcp__fundamentals__get_full_statement",
    "mcp__fundamentals__get_earnings",
    "mcp__fundamentals__get_holders",
    "mcp__fundamentals__get_price",
    "mcp__fundamentals__search_fundamentals",
]
```

Tests (REQUIRED per user):
- `tests/unit/test_fundamentals_mcp.py` — each tool returns expected shape
  against recorded fixtures (mock yfinance). Must cover cache-hit + cache-miss
  paths.
- `tests/integration/test_fundamentals_live.py` — gated by
  `PRAXIS_TEST_LIVE_YF=1`, exercises against real yfinance for a stable
  ticker like AAPL. Verifies end-to-end with real data.

### D26. Remove dead code — `depends_on`
Every enqueue site and task row currently populates `Task.depends_on`, but
`claim_next_task` never reads it. Options discussed:
- Wire it into the claim query (adds complexity for a feature we're not using)
- Remove it (cleaner)

Per user direction: **remove it.** Delete from:
- `praxis_core/db/models.py::Task` (column + index if any)
- `praxis_core/tasks/enqueue.py` (param + INSERT)
- Every call site passing `depends_on=...`
- Alembic migration to drop the column (new `0003_drop_depends_on.py`)

Keep `resource_key` as the sole serialization mechanism for now.

### D27. synthesize_memo — validate investigation quality
Currently `synthesize_memo` marks the investigation `resolved` unconditionally
if the LLM finishes. Tighten:

1. Before marking resolved, check:
   - At least `dive_financial_rigorous` produced a non-trivial output
     (≥1000 chars) AND did not STOP on INVESTABILITY (or if it did, the
     override was applied).
   - At least 2 specialists (including financial_rigorous) have outputs on
     disk.
2. If checks fail: mark investigation `partial`, emit event, do NOT set
   `status=resolved`. The memo still gets written (short memo is fine;
   "Too Hard" is a valid outcome) but the investigation record reflects
   actual quality.
3. If INVESTABILITY was STOP and not overridden, memo must cite the STOP
   reason in its opening paragraph and set `decision: "Too Hard"` in
   memo.yaml.

### D28. Clear, reviewable system prompts — refactor to `handlers/prompts/`
Currently system prompts are inline top-level constants in each handler file,
but they're interleaved with handler logic which makes them harder to review
as a set.

**New layout:**
```
handlers/
  prompts/
    __init__.py
    _prefix.py                # SYSTEM_PROMPT_PREFIX (moved from _common.py)
    triage_filing.py          # just the prompt constants
    analyze_filing_screen.py  # Haiku screen prompt (Section A)
    analyze_filing.py         # Sonnet analysis prompt (Section A)
    dive_financial_rigorous.py
    dive_business_moat.py
    dive_industry_structure.py
    dive_capital_allocation.py
    dive_geopolitical_risk.py
    dive_macro.py
    dive_custom.py            # template with specialty/focus substitution
    orchestrate_dive.py
    synthesize_memo.py
    compile_to_wiki.py        # keep for future wiring
    lint_vault.py
    generate_daily_journal.py
```

Each file: `SYSTEM_PROMPT: str = "..."` and optional `USER_PROMPT_TEMPLATE` if
appropriate. Handler files import: `from handlers.prompts.dive_business_moat
import SYSTEM_PROMPT as BUSINESS_MOAT_SYSTEM_PROMPT`.

This gives you a single directory to browse where every prompt is visible at
a glance — addresses the "want to see these clearly" concern.

### D29. Port copilot specialist prompts verbatim
For each of the 6 non-custom dive specialists, port the corresponding
specialist section from copilot's `research_prompt.py:210-284` as the
SYSTEM_PROMPT base, adapted to the praxis-v2 output conventions (wiki
linking, atomic writes, output file path). Add:
- Reference to the vault schema (appended via `read_vault_schema()`)
- The INVESTABILITY output line requirement (for `dive_financial_rigorous`)
- Budget scaling from `ResearchBudget` injected in the user prompt
- The "second-order thinking" clauses from `research_prompt.py:500-519`
  embedded in every specialist prompt (these are gold and should be reused)

### D30. Global rules + scope discipline — port from copilot
Copilot's `research_prompt.py` has a "Global Rules" section (lines 463-497)
covering source priority, disallowed sources, no-invented-data, traceability,
scope discipline, decision hygiene, output efficiency. Bundle these into
`handlers/prompts/_global_rules.py` and prepend to every dive specialist's
system prompt alongside `SYSTEM_PROMPT_PREFIX`.

---

### File-by-file change list (Section B)

#### New files
- `handlers/prompts/` directory — 14 files per D28
- `handlers/prompts/_prefix.py` — current `SYSTEM_PROMPT_PREFIX` moved here
- `handlers/prompts/_global_rules.py` — ported from copilot per D30
- `handlers/dive_industry_structure.py`
- `handlers/dive_capital_allocation.py`
- `handlers/dive_geopolitical_risk.py`
- `handlers/dive_macro.py`
- `handlers/dive_custom.py`
- `handlers/dive_business_moat.py` — new, replaces dive_business + dive_moat
- `praxis_core/research/__init__.py`
- `praxis_core/research/budget.py` — port of copilot's `ResearchBudget`
- `praxis_core/vault/coverage.py` — `find_existing_coverage()` per D24
- `services/mcp/fundamentals/__init__.py`
- `services/mcp/fundamentals/server.py` — yfinance-backed MCP server
- `services/mcp/fundamentals/tools.py` — tool implementations
- `services/mcp/fundamentals/cache.py` — Postgres cache wrapper
- `alembic/versions/0003_drop_depends_on.py`
- `alembic/versions/0004_investigation_priority.py` — adds research_priority
  int default 5
- `alembic/versions/0005_fundamentals_cache.py`

#### Modified files
- `praxis_core/schemas/task_types.py` — add new TaskType values, remove
  `DIVE_BUSINESS`, `DIVE_MOAT`, rename `DIVE_FINANCIALS` → `DIVE_FINANCIAL_RIGOROUS`.
  Update `MODEL_TIERS`, `TASK_RESOURCE_KEYS`.
- `praxis_core/schemas/payloads.py`:
  - Delete `DiveBusinessPayload`, `DiveMoatPayload`, `DiveFinancialsPayload`
  - Add `DiveFinancialRigorousPayload`, `DiveBusinessMoatPayload`,
    `DiveIndustryStructurePayload`, `DiveCapitalAllocationPayload`,
    `DiveGeopoliticalRiskPayload`, `DiveMacroPayload`, `DiveCustomPayload`
  - Add `research_priority: int = 5` to `OrchestrateDivePayload`
- `praxis_core/tasks/validators.py` — add validators for each new dive type;
  INVESTABILITY-aware validator for `dive_financial_rigorous`
- `praxis_core/tasks/enqueue.py` — remove `depends_on` param + column write
- `praxis_core/db/models.py` — drop `Task.depends_on` column; add
  `Investigation.research_priority`; add `FundamentalsCache` table
- `handlers/orchestrate_dive.py` — rewrite per D22 (wiki crawl, coverage
  check, expanded plan types, custom specialist planning)
- `handlers/_plan_parser.py` — extend to parse `dive_custom specialty=...`
  syntax + multi-line specialist entries (why/focus)
- `handlers/synthesize_memo.py` — tighten validation per D27
- `handlers/_common.py` — move `SYSTEM_PROMPT_PREFIX` to
  `handlers/prompts/_prefix.py` (keep an alias import for backward compat);
  add `DIVE_ALLOWED_TOOLS` with MCP fundamentals tool names; export
  `run_llm_with_fundamentals()` helper that uses DIVE_ALLOWED_TOOLS
- `services/dispatcher/worker.py` — post-dive INVESTABILITY detection and
  sibling-cancel logic per D20
- `services/mcp/server.py` — add `override_investability()` tool per D20;
  update `open_investigation` to accept `research_priority`
- `vault_seed/.mcp-config.json.template` — add fundamentals MCP server entry
- `infra/render_mcp_config.sh` — render fundamentals MCP config into
  `<vault>/.mcp-config.json`
- `handlers/__init__.py` / handler registry — register all new dive handlers

#### Deleted files
- `handlers/dive_business.py` (replaced by `dive_business_moat.py`)
- `handlers/dive_moat.py` (merged into `dive_business_moat.py`)
- `handlers/dive_financials.py` (renamed to `dive_financial_rigorous.py`)
- `handlers/_dive_base.py` — reconsider during implementation; if the new
  handlers diverge enough from the old shared base, delete it; otherwise port

---

### Prompt drafts (verbatim ports from copilot + praxis-v2 adaptations)

For each specialist, I'll implement following this template:

```python
# handlers/prompts/dive_business_moat.py
SYSTEM_PROMPT = """
<copilot's business-moat-analyst specialist section>
<praxis-v2 adaptations:
  - output file path convention
  - wiki frontmatter requirements
  - atomic write via Write tool
  - word limit injected from ResearchBudget>
<global rules from D30>
<second-order thinking clauses>
""".strip()
```

Full drafts will be written during implementation from copilot's
`research_prompt.py:254-284` specialist paragraphs + the global rules.
Each gets its own prompt file so Avyuk can review at
`handlers/prompts/dive_*.py` after.

**`dive_custom` prompt template** — needs a different structure since part
of the prompt is generated by the orchestrator:

```python
SYSTEM_PROMPT_TEMPLATE = """
You are a specialist analyst for a specific investment question. Your
specialty for this investigation is:

## Specialty: {specialty}

## Why this specialist was spawned:
{why}

## What to focus on:
{focus}

<global rules>
<second-order thinking>
<word limit, output path, atomic-write discipline>

Produce your analysis at <vault>/companies/<TICKER>/dives/{specialty}.md.
"""
```

---

### Implementation order (Section B, runs concurrent with Section A tonight)

1. **Dead code removal first (fast, unblocks later steps).**
   - Delete `depends_on` — migration, model, enqueue, callers
2. **New schemas + task types.** TaskType enum, payloads, validators stubs.
3. **Alembic migrations.** 0003 (drop depends_on), 0004 (investigation
   priority), 0005 (fundamentals cache).
4. **`praxis_core/research/budget.py`** — port ResearchBudget verbatim.
5. **`praxis_core/vault/coverage.py`** — find_existing_coverage heuristic.
6. **Fundamentals MCP** — server, tools, cache, MCP config integration.
7. **Fundamentals MCP tests** — unit (mocked) + integration (live, gated).
8. **`handlers/prompts/` directory** — move existing prompts, write new
   specialist prompts from copilot sources + adaptations.
9. **Specialist handlers** — financial_rigorous, business_moat, industry,
   capital_allocation, geopolitical, macro, custom. Each thin (assembles
   prompt + calls run_llm + returns result).
10. **Orchestrator rewrite** — wiki crawl, coverage, expanded plan types,
    custom specialist planning.
11. **Plan parser extension** — multi-line entries with specialty/why/focus.
12. **Worker INVESTABILITY logic** — detect STOP, cancel siblings, emit
    events.
13. **MCP `override_investability` tool.**
14. **synthesize_memo tightening** — quality checks per D27.
15. **Tests** — new dive validators, plan parser extensions, coverage
    helper, worker INVESTABILITY cancel behavior, override tool, synthesize
    quality gates.
16. **Integration smoke** — run a full auto-dive on a real ticker end to
    end: orchestrate → 4 specialists + 1 custom → synthesize. Verify
    INVESTABILITY line parsed. Verify sibling cancel on simulated STOP.
    Verify override re-enqueues.
17. **Commit.**

---

### HITL mechanisms (flagged for later clarification — Section C)

User flagged: "INVESTABILITY GATE SHOULD BE HIGHLY AUDITABLE AND OVERRIDABLE
BY THE HUMAN THO BY SOME MECHANISM -- WE NEED TO CLARIFY THESE HITL
MECHANISMS A BIT LATER."

Captured for a future Section C (human-in-the-loop controls). Monday ship
includes:
- Events for every INVESTABILITY decision (`investability_stop`,
  `investability_overridden`, `investability_malformed`)
- `override_investability` MCP tool (observer-callable)
- Investigation file appends a `## Human override` log on every override
- Dashboard surfacing of STOP'd investigations is follow-up (not blocking)

Full HITL design (prune mechanism for running tasks, ticker-level pause,
investigation cancel cascades, etc.) is a separate upcoming conversation.

---

### Things NOT in this pass (flagged for FOLLOWUPS)

- **Semantic coverage search** (pgvector) — D24 uses keyword matching.
  Upgrade later.
- **Full `ResearchBudget.web_lookups` enforcement** — we don't have web
  tools in the worker yet. The cap is descriptive in prompts but not
  enforced. Wire up WebSearch/WebFetch later with per-task quota.
- **Subagents inside a single dive** — copilot can have a coordinator
  spawn Claude Code subagents within one session. We keep
  one-dive-per-Postgres-task for crash safety. Hybrid upgrade (MCP tool
  that lets a handler spawn bounded subagents) is deferred.
- **Full memo.yaml schema parity with copilot** — copilot's memo.yaml has
  thesis_summary, decision, valuation (fair_value_estimate, entry_range,
  exit_range, key_assumptions, invalidation), scores (tactical,
  fundamental), dependencies (data_vintage). synthesize_memo output should
  match. Port as part of D29 prompt writing.
- **`draft_monitors.yaml`** — copilot's memo pass also produces monitor
  proposals. Out of scope for Section B; revisit when we design the
  monitors subsystem.
- **Tactical mode** (`--tactical` flag in copilot's research pipeline) —
  Out of scope.

---

### Open items

None remaining. All parity-with-copilot decisions default to "port copilot's
behavior unless explicitly flagged otherwise."

---

### Status (Section B)

- [x] D19-D30 decisions locked in (2026-04-19 late eve review)
- [x] Dead code removal (`depends_on`)
- [x] New task types + payloads + validators
- [x] Alembic migrations
- [x] `ResearchBudget` ported
- [x] `find_existing_coverage` helper
- [x] Fundamentals MCP server + tools
- [x] Fundamentals MCP cache
- [x] Fundamentals MCP tests (unit + integration-live-gated)
- [x] `handlers/prompts/` directory scaffolded
- [x] All existing prompts moved to prompts/
- [x] Six specialist prompts ported from copilot
- [x] `dive_custom` prompt template
- [x] Global rules + second-order-thinking port
- [x] Six new specialist handlers
- [x] `dive_custom` handler
- [ ] Orchestrator rewrite (wiki crawl + coverage + expanded plan)
- [x] Plan parser extension (dive_custom multi-line)
- [x] Worker INVESTABILITY detection + sibling cancel
- [x] `override_investability` MCP tool
- [x] synthesize_memo quality gates
- [ ] Tests green (specialist validators, plan parser, coverage, worker
      logic, override, synthesize gates)
- [ ] Integration smoke (full auto-dive end-to-end)
- [ ] Commit

---

## Section C — MCP control-plane for running investigations

Captured 2026-04-19 late evening. The MCP server is how observer Claude (and
by extension Avyuk) reaches into a running system. Today, several of its
"write" tools don't actually do what their name suggests — they flip DB flags
that nothing downstream reads. Section C fixes the four highest-impact gaps.

This is the foundation for HITL. Future HITL work (ticker-level pause,
bulk prune, investigation cancel cascades with confirmation UX, persistent
ticker-boost) will build on these primitives. Section C must ship tomorrow
alongside A and B — without working cancel and browsability, running
investigations can't be controlled from observer chat, which is the core
"human manager" muscle.

**Scope note (Avyuk, 2026-04-19 late eve):**
- Multiple underlying MCP calls are fine as long as the observer-chat UX is
  single-intent (e.g. "cancel the NVDA dive" → observer Claude orchestrates
  whatever tool calls are needed). The `cancel_investigation` primitive
  means observer typically only needs one call anyway.
- **Delete `pause_investigation` and `resume_investigation` entirely** —
  they don't do anything today and "fixing" them isn't worth the surface
  area right now. If you want to pause, use `cancel_investigation` and
  `open_investigation` later. Long-running investigations (weeks of
  accruing dives) aren't a real use case yet; revisit pause semantics when
  they are.
- `boost_ticker` stays one-shot for now — persistence across new work is
  flagged for future Section D.

---

### Design decisions (locked in)

### D31. `cancel_task` — actually stop running work
Current bug: `cancel_task` sets `status='canceled'` in the DB but the worker
keeps going. If the handler completes ~seconds later, `mark_success/partial/
failed` unconditionally overwrites the canceled row.

**Two-part fix:**

**C31.a — Race-proof the status transition.** In `praxis_core/tasks/lifecycle.py`,
every terminal-state setter gets a status guard in the WHERE clause:

```sql
-- mark_success
UPDATE tasks SET status='success', ...
WHERE id=:task_id AND status='running'

-- mark_partial
UPDATE tasks SET status='partial', ...
WHERE id=:task_id AND status='running'

-- mark_failed
UPDATE tasks SET status='failed', ...
WHERE id=:task_id AND status='running'

-- mark_dead_letter
UPDATE tasks SET status='dead_letter', ...
WHERE id=:task_id AND status IN ('running', 'failed')
```

If status is already `canceled`, the UPDATE affects 0 rows and the worker's
write is silently ignored. The canceled state wins.

**C31.b — Worker observes cancellation and tears down the subprocess.**

In `services/dispatcher/worker.py`:

1. Add a **cancel-watch loop** running alongside the heartbeat loop, polling
   `tasks.status` every 5s (configurable via
   `WORKER_CANCEL_POLL_INTERVAL_S`, default 5). If it observes
   `status='canceled'`, sets an `asyncio.Event` named `cancel_event`.

2. Change the handler-execution pattern to make the handler coroutine
   cancellable:
   ```python
   handler_task = asyncio.create_task(handler(ctx))
   cancel_waiter = asyncio.create_task(cancel_event.wait())
   done, pending = await asyncio.wait(
       {handler_task, cancel_waiter},
       timeout=wall_clock_timeout_s,
       return_when=asyncio.FIRST_COMPLETED,
   )
   if cancel_waiter in done:
       handler_task.cancel()
       try:
           await handler_task
       except asyncio.CancelledError:
           pass
       handler_error = ("canceled via MCP tool", False)
       ...
   ```

3. CLIInvoker already has a `finally` that calls `_kill_proc_tree()` — this
   fires on `asyncio.CancelledError` propagation, so the `claude -p`
   subprocess (and any child processes via the new-session killpg) gets
   SIGTERM'd within seconds.

4. Worker's outer result path: on cancel, skip `mark_success`/etc entirely.
   Emit `event_type="task_canceled_observed"` for audit. Return cleanly.

**Tightening cancel latency:** current heartbeat interval is 60s. A canceled
task could run up to 60s past the cancel call if we reused the heartbeat.
The dedicated 5s cancel-watch loop brings worst-case latency to ~5-10s,
which is responsive enough for interactive use.

### D32. Delete `pause_investigation` / `resume_investigation`
The tools are misleading: they flip `investigations.status='paused'` but
nothing downstream respects that flag. Users reaching for "pause" in a
real moment would get no actual behavior. Rather than fix the plumbing,
**remove them** — the `cancel_investigation` primitive (D33) covers the
realistic stop-a-dive-chain use case. Long-running pause semantics can be
reintroduced in Section D if/when investigations become durable multi-week
entities (not the case today — a dive chain runs for minutes, not days).

**Deletes:**
- `@mcp.tool() async def pause_investigation(handle)` — remove from
  `services/mcp/server.py`
- `@mcp.tool() async def resume_investigation(handle)` — remove
- Remove `'paused'` from the documented status values on
  `investigations.status` (it's plain TEXT, no DB enum constraint, so no
  migration required — just update model-level docs/comments and any
  handler code that checked for `status='paused'`)
- Remove the `claim_next_task` pause-gating clause from the original
  D32 plan — **not added.** No investigation-status gating in dispatch
  for Monday; investigations are either active (dispatchable) or one of
  the terminal states (resolved / abandoned) which imply the chain is
  done anyway.

**MCP surface after deletion:** observer has `open_investigation`,
`cancel_investigation` (D33), `list_investigations` (D34),
`read_investigation`. No pause. If you want to "hold" an investigation,
`cancel_investigation` it and `open_investigation` a fresh one later.
Slightly more expensive (you re-run orchestrate + the dives) but clean
semantics, no stale state.

**Audit cleanup:** check for any places that reference `'paused'` as a
valid investigation status and remove:
- `praxis_core/db/models.py` docstrings / comments
- `services/mcp/server.py::list_investigations` — `status` param
  Literal should be `"active" | "resolved" | "abandoned"` only
- PLAN.md §6 schema comment — update the inline comment documenting the
  status values (in a follow-up; not code-blocking)

### D33. `cancel_investigation(handle, *, cascade: bool = True)` — new tool
Stop an entire investigation chain in one call.

**Behavior:**
1. Load investigation by handle. Error if not found.
2. If `cascade=True` (default):
   - Find all tasks where `investigation_id=inv.id` AND
     `status IN ('queued', 'partial', 'running')`.
   - For each, set `status='canceled'` with `finished_at=now()`,
     `last_error='investigation_canceled'`.
   - For running tasks, the worker's cancel-watch loop (D31.b) observes
     within 5-10s and tears down the subprocess.
3. Set `investigation.status='abandoned'` (not 'canceled' — 'abandoned' is
   the terminal state already in the investigations enum per PLAN §6).
4. Set `investigation.resolved_at=now()`.
5. Emit `event_type="investigation_canceled"` with handle, affected task
   count, cascade flag.
6. Append `## Canceled` section to `investigations/<handle>.md` with
   timestamp and reason.

If `cascade=False`: just sets investigation status to abandoned. Running
tasks keep running until completion. Useful when Avyuk wants to mark an
investigation as "done, no more follow-up" without killing in-flight work.

**Return value:** `{ok, handle, affected_tasks, status}`.

### D34. `list_investigations(status?, limit)` — new tool
Browse active investigations from observer chat without needing to know
handles.

```python
@mcp.tool()
async def list_investigations(
    status: Literal["active", "paused", "resolved", "abandoned"] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List investigations. Default: all, sorted by last_progress_at DESC."""
    async with session_scope() as session:
        q = select(Investigation)
        if status:
            q = q.where(Investigation.status == status)
        q = q.order_by(desc(Investigation.last_progress_at)).limit(limit)
        rows = (await session.execute(q)).scalars().all()
        return [
            {
                "handle": inv.handle,
                "status": inv.status,
                "scope": inv.scope,
                "initiated_by": inv.initiated_by,
                "hypothesis": inv.hypothesis,
                "created_at": inv.created_at.isoformat() if inv.created_at else None,
                "last_progress_at": inv.last_progress_at.isoformat() if inv.last_progress_at else None,
                "resolved_at": inv.resolved_at.isoformat() if inv.resolved_at else None,
                "task_counts": await _investigation_task_counts(session, inv.id),
            }
            for inv in rows
        ]
```

Helper `_investigation_task_counts(session, inv_id)` returns a small dict
like `{queued: 2, running: 1, success: 3, partial: 0, failed: 0, canceled: 0}`.
Cheap GROUP BY status query. Gives observer a one-look summary.

### D35. Heartbeat loop + cancel-watch share a coroutine group
To avoid orphaning tasks on worker shutdown, the cancel-watch and heartbeat
loops are spawned in the same `asyncio.TaskGroup` (Python 3.11+) as the
handler coroutine. When the handler completes (or errors), the group
cancels both loops cleanly. This is a small structural cleanup in
`execute_task` — not a behavioral change, just makes the lifecycle
explicit.

### D36. `last_progress_at` maintenance
For `list_investigations` to surface the freshest investigations, we need
`investigations.last_progress_at` to actually update. Audit:
- `orchestrate_dive` handler should `UPDATE investigations SET
  last_progress_at=now() WHERE handle=?` on successful plan emission.
- Each dive handler (`dive_financial_rigorous`, etc.) should do the same
  on successful artifact write.
- `synthesize_memo` already updates `resolved_at`; should also bump
  `last_progress_at`.

Add a small helper `async def touch_investigation(session, investigation_id)`
in `praxis_core/db/models.py` or an adjacent utility module. Call from
each dive + orchestrate_dive + synthesize.

---

### File-by-file change list (Section C)

#### Modified
- `praxis_core/tasks/lifecycle.py`:
  - `mark_success`: add `AND status='running'` to WHERE
  - `mark_partial`: add `AND status='running'` to WHERE
  - `mark_failed`: add `AND status='running'` to WHERE
  - `mark_dead_letter`: add `AND status IN ('running','failed')` to WHERE
  - `claim_next_task`: **no** investigation-pause clause (D32 deleted pause)

- `services/dispatcher/worker.py`:
  - New `_cancel_watch_loop(task_id, cancel_event, stop)` coroutine
  - Rework `execute_task` to race handler vs cancel_event via
    `asyncio.wait` / `TaskGroup`
  - On cancel, emit `task_canceled_observed` event, skip terminal-state
    writes, let DB remain in `canceled` state

- `services/mcp/server.py`:
  - **Delete** `pause_investigation` tool
  - **Delete** `resume_investigation` tool
  - Add `cancel_investigation(handle, cascade=True)` tool
  - Add `list_investigations(status?, limit)` tool with task-count helper
    (status Literal excludes 'paused')

- `praxis_core/config.py` (or settings module):
  - Add `worker_cancel_poll_interval_s: int = 5`

- `handlers/orchestrate_dive.py`, each `handlers/dive_*.py`,
  `handlers/synthesize_memo.py`:
  - Call `touch_investigation(session, investigation_id)` on success

- `praxis_core/db/models.py` OR `praxis_core/tasks/investigations.py` (new):
  - Add `touch_investigation(session, investigation_id)` helper

#### New tests
- `tests/integration/test_cancel_task_running.py` — mock a slow handler,
  call cancel_task mid-flight, assert:
  - Handler is interrupted within ~10s
  - Task ends in `canceled` state (not `success`)
  - Subprocess was SIGTERM'd
  - `task_canceled_observed` event emitted
- `tests/unit/test_pause_tool_removed.py` — imports `services.mcp.server`
  and asserts `pause_investigation` and `resume_investigation` are not in
  the tool registry. (Tiny sanity check that the deletion is complete.)
- `tests/unit/test_mark_status_race.py` — unit tests for each `mark_*`
  function showing that a pre-set `canceled` row is not overwritten.
- `tests/unit/test_cancel_investigation.py` — cascade + non-cascade
  behavior, affected task counts.
- `tests/unit/test_list_investigations.py` — filters by status, limit,
  ordering.

---

### Implementation order (Section C)

1. `lifecycle.py` terminal-setter status guards + unit tests
2. Delete `pause_investigation` + `resume_investigation` MCP tools + tool-
   removed sanity test
3. `touch_investigation` helper + wire into orchestrate/dive/synthesize
4. Worker `_cancel_watch_loop` + racing handler execution + integration
   test with a slow handler
5. `cancel_investigation` MCP tool + tests
6. `list_investigations` MCP tool + tests
7. Manual smoke: open an investigation, let orchestrator plan, start a
   dive, call `cancel_investigation` from a Python REPL, verify the dive
   actually stops and all siblings mark canceled.
8. Commit.

---

### HITL foundations for Section D (future)

Not in this pass but enabled by Section C's primitives:
- **Real pause semantics, if/when needed.** Today's investigations are
  short-lived (minutes), so cancel-and-reopen is acceptable. If durable
  multi-week investigations become a use case, reintroduce a `hold_*`
  verb (not `pause_*` — different name to avoid confusion) with proper
  dispatch gating. Design the gating mechanism then.
- **Ticker-level bulk actions** — cancel every active investigation for a
  ticker in one call. Chain of `list_investigations(status="active")`
  filtered by ticker → `cancel_investigation` each.
- **Persistent `boost_ticker`** — current tool is one-shot. Future version:
  write a row to a `ticker_boost` table with a TTL; enqueue logic checks it
  and applies the boost to new tasks. Avyuk explicitly OK'd the one-shot
  behavior for Monday; durable version is Section D work.
- **Bulk prune** — cancel all queued tasks older than N hours, or all
  investigations initiated by a specific signal type if it's proven
  low-value.
- **Confirmation UX for cascading cancels** — dry-run mode that reports
  "this will cancel X tasks in Y investigations" before executing.
- **Audit trail viewer** — dashboard section showing cancel/override/
  boost events over time with the human who triggered each.
- **`set_investigation_priority(handle, new_priority)`** — raise/lower
  research_priority mid-flight; re-derives ResearchBudget for any
  not-yet-started dives.
- **Watchlist-aware autocancel** — auto-cancel dives for tickers Avyuk
  explicitly unsubscribes from.

These get their own section when we design HITL fully.

---

### Open items

None for Monday. HITL clarification (the bigger design) is Section D
territory — Section C's four tools are the load-bearing primitives and are
locked in.

---

### Status (Section C)

- [x] D31-D36 decisions locked in
- [x] `lifecycle.py` terminal-setter status guards
- [x] Delete `pause_investigation` + `resume_investigation` MCP tools
- [x] `touch_investigation` helper + call-site wiring
- [x] Worker cancel-watch loop + handler racing
- [x] `cancel_investigation` MCP tool
- [x] `list_investigations` MCP tool (no 'paused' in status literal)
- [x] Config: `worker_cancel_poll_interval_s`
- [ ] Unit tests (mark_* races, cancel_investigation, list_investigations,
      pause-tools-removed sanity)
- [ ] Integration test (cancel-running-task)
- [ ] Manual smoke
- [ ] Commit

---

## Section D — compile_to_wiki hardening + idea surfacing MVP

Captured 2026-04-19 late evening after Avyuk asked to actually ship compile
and idea surfacing in working state. **Explicit override of PLAN.md §16:
Loop C graph-walk tasks move from "deferred to week 2" to "ships Monday as
part of MVP."** The rationale: the vault's compounding value only works if
there's active ideation on top of it, and shipping ingest + dives without
that means the system surfaces individual signals but misses the
cross-cutting patterns that make a research org valuable. Don't defer the
part that creates the leverage.

This section has two parts. Part 1 hardens `compile_to_wiki` and wires it
back into the trade-relevant path (it was orphaned by Section A's
decoupling decision, which we're partially reversing here with new
safeguards). Part 2 introduces a new `surface_ideas` task type that reads
the accumulating wiki + recent analyses and emits ranked "angles worth
thinking about" — with phone-push integration for high-urgency surfaces.

---

### Part 1 — compile_to_wiki audit + hardening

### D37. Trigger wiring — compile fires on trade_relevant
Reverse Section A's "compile is decoupled" stance. `analyze_filing`'s
downstream enqueue fan-out (D7) adds a third enqueue:

```python
# in handlers/analyze_filing.py, inside trade_relevant branch:
if result.ticker:   # compile only if ticker known
    await enqueue_task(
        session,
        task_type=TaskType.COMPILE_TO_WIKI,
        payload={
            "source_kind": "filing_analysis",
            "analysis_path": str(analysis_json_rel_path),
            "ticker": result.ticker,
            "accession": result.accession,
        },
        priority=1,      # P1: just behind the P0 analyze but ahead of P2 dives
        dedup_key=f"compile:{result.form_type}:{result.accession}",
        resource_key=f"company:{result.ticker}",   # serialize per company
    )
```

**Threshold:** compile fires whenever notify does — same `trade_relevant`
gate. Rationale: if it's worth a phone push, it's worth capturing in the
wiki. No separate compile threshold. Simpler and avoids "invisible but
notified" filings.

**Why compile runs BEFORE the dive completes:** dives take 5-10 min; compile
is ~30s. Running compile first means when the dive's specialists crawl
`companies/<TICKER>/notes.md` at start, they see the fresh analysis
summary already folded in. Mild improvement in dive context quality.

### D38. Pre-write backup — protect existing notes
Before the LLM writes to `companies/<TICKER>/notes.md`, the handler stashes
the current version (if any) to:

```
_backups/compile/<YYYY-MM-DD>/<et-HHMMSS>-<ticker>-notes.md
```

New helper: `praxis_core/vault/backup.py::stash_for_edit(path) -> Path`.
Returns the backup path, creates parent dirs, copies atomically (reuse
`atomic_write` for the copy write).

Validator addition: check notes.md did not SHRINK by more than 25% vs the
backup. If it did → malformed (LLM wiped content). Remediation task
enqueued with the backup path included so a re-run can reference the prior
state.

Backup retention: 30 days. A weekly scheduled task cleans older backups.
(Add to existing `cleanup_sessions` or create a new `cleanup_backups` —
use the existing one by extending its scope.)

### D39. Decouple INDEX.md from per-compile — LOG only
Per-compile INDEX.md edits race across tickers (resource_key is per-ticker,
doesn't cover INDEX). Fix: compile no longer touches INDEX. Compile only:

1. Updates `companies/<TICKER>/notes.md`
2. Appends to `companies/<TICKER>/journal.md`
3. Appends to `LOG.md` (atomic append — no race since append is additive)
4. May touch themes/concepts/ if the analysis references them (these are
   protected by their own not-yet-enforced resource_keys — see D41)

Validator change: drop the INDEX.md existence requirement.
`refresh_index` (scheduled task, already exists, Haiku) rebuilds INDEX
from scratch periodically. Bump its cadence from whatever it is today to
every 15 min during market hours. Cheap — Haiku + deterministic scan.

### D40. [REMOVED — per OD3] No consolidation for Monday
Originally this was "auto-compact notes.md when it exceeds 50KB."
Dropped per Avyuk's call — wiki size is not a concern for the MVP. The
pre-write backup from D38 stays (it's failure-recovery, separate from
consolidation). If notes files eventually grow unwieldy, consolidation
can be reintroduced as a separate maintenance task with proper approval
semantics.

### D41. Theme/concept write serialization
compile_to_wiki may touch theme and concept files when the analysis
references them. Today these have `resource_key = "theme:<handle>"` /
`"concept:<handle>"` declared in `TASK_RESOURCE_KEYS`, but compile_to_wiki
itself holds `resource_key = "company:<TICKER>"` — it writes to theme
files without holding the theme lock.

For Monday: **accept this race.** The compile prompt already says "add a
dated bullet to theme's Evidence section" — append-style operations are
naturally commutative. If two compiles both append to the same theme
evidence list, the last writer wins for that single edit but no content
is lost (each compile reads + appends + writes via `atomic_write` = both
bullets end up present if the reads/writes interleave correctly).

Flag as a FOLLOWUPS item: proper multi-resource locking (compile holds
company + theme + concept locks simultaneously before starting) is the
right long-term fix. Not blocking.

### D42. LOG.md rotation
When `LOG.md` exceeds 5MB, rotate:
- Move current → `LOG.archive-<YYYYMMDD>.md`
- Start fresh `LOG.md` with a header line

Add to scheduler: runs daily at 03:00 ET. Cheap, deterministic.
(New scheduler entry in `services/scheduler/main.py`.)

### D43. Strict citation format in validator
Current validator checks `payload.analysis_path` appears as a substring in
notes.md. Tighten: require it appears as a wikilink `[[<analysis_path>]]`
with literal brackets. Regex-match, not substring.

Catches cases where the LLM pastes the path as plain text and breaks
Obsidian's graph view.

---

### Part 2 — Idea surfacing MVP (override PLAN §16)

### D44. `surface_ideas` — new task type
New Sonnet task type. Reads recent vault activity and emits a list of
candidate "angles worth thinking about." Explicitly a cross-cutting
synthesizer — different from `notify` (which is per-analysis, mechanical)
and from `dive` (which is deep but single-ticker).

**Schema (`AnalysisResult` lives alongside this in artifacts.py):**

```python
class SurfacedIdea(BaseModel):
    handle: str                          # stable slug, e.g. "uranium-pack-20260420"
    dedup_handle: str                    # hash of (idea_type, sorted-tickers, sorted-themes)
    idea_type: Literal[
        "theme_intersection",            # ticker X exposed to multiple active themes
        "cross_ticker_pattern",          # similar signal across multiple tickers
        "thesis_revision",               # existing thesis needs re-examination
        "question_answered",             # open question has answer in recent analysis
        "concept_promotion",             # recurring pattern should become a concepts/ entry
        "anomaly",                       # doesn't fit existing frame — worth a look
    ]
    tickers: list[str]
    themes: list[str]
    summary: str                         # 1-2 sentences — the "what"
    rationale: str                       # 2-3 sentences — the "why it matters"
    evidence: list[str]                  # list of vault paths supporting the idea
    urgency: Literal["low", "medium", "high"]
    surfaced_at: str                     # ET ISO


class SurfacedIdeaBatch(BaseModel):
    batch_handle: str                    # e.g. "batch-20260420-1430"
    generated_at: str
    ideas: list[SurfacedIdea]
    inputs_summary: dict[str, int]       # {analyses_read: 12, themes_scanned: 8, ...}
```

### D45. Triggers for `surface_ideas` — 24/7
Three triggers:

**Scheduled (round-the-clock — per OD2):**
- Every 30 min, always. All days, all hours. Off-hours ideation is
  explicitly desired per Avyuk ("offhours are the best time to ideate").
- Special markers on top of the 30-min cadence:
  - 08:30 ET: morning digest emission pass (compose overnight ntfy
    digest per D50; the surface run itself is just the regularly
    scheduled one that happens to land near 08:30)
  - 17:00 ET: evening review pass (another regular run; no special
    behavior, just flagged in logs as the post-close view)

**Event-driven:**
- When the rolling 24h count of `trade_relevant` analyses increments past
  every N=5 (i.e., after 5, 10, 15, ... analyses accumulate) enqueue a
  fresh surface run. Dedup via `surface:event:<YYYY-MM-DD>:<bucket>`
  where bucket = `floor(count/5)`.

**On-demand:**
- MCP tool `surface_ideas_now(focus: str | None = None)` — observer can
  trigger a one-off. Optional `focus` string hints the surface prompt
  (e.g., "focus on energy names"). Enqueued at priority 1 (observer
  request lane).

### D46. Handler behavior — `handlers/surface_ideas.py`
1. Gather inputs (pure Python, no LLM):
   - Recent analyses: read `sources` + `tasks(type=analyze_filing, status=success, finished_at > now-24h)` → list of recent analysis.json paths
   - Recent trade-relevant signals: `signals_fired(fired_at > now-24h)`
   - Recent themes: `themes/*.md` with `mtime > now-7d`
   - All concepts: `concepts/*.md` (evergreen — no time filter)
   - Open questions: `questions/*.md` where frontmatter status is not "resolved"
2. Build a compact summary doc for the Sonnet call (~5K tokens max) with:
   - Last 24h of signals: ticker, classification, magnitude, 1-line summary
   - Active theme titles + 1-line summaries
   - Open question titles
3. Call Sonnet with a specialized prompt (see D47) asking for up to 10
   ranked ideas matching the schema. Use structured JSON output.
4. Parse response → list of `SurfacedIdea` objects.
5. Apply dedup:
   - For each idea, compute `dedup_handle`
   - Query `surfaced_ideas` table: any row with same `dedup_handle`
     within 24h? If yes AND the evidence hash matches → skip (already
     surfaced)
   - If evidence changed materially (new paths in evidence set) → allow
     re-surface
6. Persist new ideas:
   - Write batch to `_surfaced/<YYYY-MM-DD>/ideas-<HHMM>.md` (human
     readable; for observer chat + phone viewing)
   - Insert `surfaced_ideas` rows
7. For each idea with `urgency == "high"`:
   - Enqueue `notify` task with a payload derived from the idea
     (signal_type=`surfaced_{idea_type}`, body=`summary`, linked to the
     `_surfaced/` file)
   - `urgency == "medium"` ideas are added to a rolling morning-digest
     buffer; not pushed individually
   - `urgency == "low"` ideas are silent; visible via MCP / dashboard
8. Update `_surfaced/current.md` — rolling digest showing last 20
   surfaced ideas across all urgency levels.

Budget: `max_budget_usd=1.00`. Sonnet. Not cheap but high-leverage.

### D47. Surface system prompt (inline here — also lives in handlers/prompts/)

```
You are the ideation layer of an investment research system. Your job is to
spot cross-cutting patterns and angles in the last 24h of system activity
that a human PM would find worth their 5 minutes of attention.

You will be given:
- A list of recent filing/PR analyses (ticker, classification, magnitude,
  one-line summary)
- Active themes in the wiki (title + summary)
- All evergreen concepts (titles)
- Open unresolved questions (titles)

Your output MUST be valid JSON matching this schema — no prose, no code
fences:

{
  "ideas": [
    {
      "idea_type": "theme_intersection" | "cross_ticker_pattern"
                 | "thesis_revision" | "question_answered"
                 | "concept_promotion" | "anomaly",
      "tickers": [str],
      "themes": [str],
      "summary": "<1-2 sentences>",
      "rationale": "<2-3 sentences — what would a PM do with this?>",
      "evidence": [str]   # vault paths from the inputs
      "urgency": "low" | "medium" | "high"
    },
    ...
  ]
}

Guidelines:
- High urgency: something a PM should see within the hour (new pattern
  across 3+ tickers; thesis-breaking evidence; answer to an open high-
  priority question). Reserve for genuinely material cross-cutting signal.
- Medium urgency: worth the morning digest. Interesting pattern, not
  urgent.
- Low urgency: background noise that shouldn't wake anyone but is worth
  logging for later review.

Spam discipline:
- Fewer high-quality ideas beats many low-quality ones
- If nothing interesting surfaced, return {"ideas": []} — don't invent
  noise
- Each idea needs concrete evidence paths — no hand-waving
- The "anomaly" category is for genuine surprise, not everything that
  doesn't fit cleanly.
- **Hard cap: maximum 1 anomaly per batch.** If you find more than one
  candidate anomaly, pick the single most consequential and omit the
  rest; those should resurface in future batches if they're still
  material.

Second-order thinking: favor ideas where the non-obvious insight is not
already captured in the existing vault. If a theme already covers an
angle, reference it but don't surface a redundant idea.
```

**Handler-side enforcement of the anomaly cap (belt + suspenders):**
After parsing the Sonnet response, if more than one idea has
`idea_type == "anomaly"`, keep only the first (highest-ranked) one and
drop the rest. Emit an `event_type="surface_anomaly_cap_enforced"`
event logging the drop count so we can see if the prompt is drifting.

### D48. DB schema — `surfaced_ideas` table
Alembic migration `0006_surfaced_ideas.py`:

```sql
CREATE TABLE surfaced_ideas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    handle TEXT UNIQUE NOT NULL,
    dedup_handle TEXT NOT NULL,
    idea_type TEXT NOT NULL,
    tickers TEXT[] NOT NULL DEFAULT '{}',
    themes TEXT[] NOT NULL DEFAULT '{}',
    summary TEXT NOT NULL,
    rationale TEXT NOT NULL,
    evidence TEXT[] NOT NULL DEFAULT '{}',
    evidence_hash TEXT NOT NULL,       -- for change detection
    urgency TEXT NOT NULL,
    surfaced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    batch_handle TEXT,
    notified BOOLEAN NOT NULL DEFAULT false   -- set when ntfy push fires
);

CREATE INDEX idx_surfaced_dedup ON surfaced_ideas (dedup_handle, surfaced_at DESC);
CREATE INDEX idx_surfaced_recent ON surfaced_ideas (surfaced_at DESC);
```

### D49. MCP tools for browsing surfaced ideas
Two new MCP tools in `services/mcp/server.py`:

```python
@mcp.tool()
async def list_surfaced_ideas(
    hours: int = 24,
    min_urgency: Literal["low", "medium", "high"] = "low",
    idea_type: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List recent surfaced ideas. Default: last 24h, all urgencies."""

@mcp.tool()
async def surface_ideas_now(focus: str | None = None) -> dict:
    """Force a fresh surface run. Returns (batch_handle, task_id). Use
    when you've just read a bunch of news and want the system to
    cross-check against its knowledge."""
```

### D50. Morning-digest ntfy at 08:30 ET
Add to scheduler: at 08:30 ET, read all `urgency={medium,high}` surfaced
ideas from the last 18h (covers evening + overnight), compose a single
consolidated ntfy push with titles + 1-liners for each. One push, not N.

Format:
```
Morning digest (N ideas):

1. [HIGH] <summary>
2. [MED]  <summary>
...

Full: <link to _surfaced/ vault path>
```

Single ntfy POST. High priority if any idea was `high`, else default.

### D51. Prompt refactor wiring
`handlers/prompts/` (from Section B D28) adds:
- `surface_ideas.py` — the D47 system prompt
- `consolidate_notes.py` — the D40 compaction prompt (draft during
  implementation)
- `compile_to_wiki.py` — lift existing prompt from handler file for
  reviewability

---

### File-by-file change list (Section D)

#### New files
- `handlers/surface_ideas.py`
- `handlers/prompts/surface_ideas.py`
- `handlers/prompts/compile_to_wiki.py` (move existing inline prompt)
- `praxis_core/vault/backup.py` — `stash_for_edit()` helper
- `praxis_core/vault/section_append.py` — `append_to_section()` helper (D52)
- `praxis_core/schemas/surfacing.py` — `SurfacedIdea`, `SurfacedIdeaBatch`
- `alembic/versions/0006_surfaced_ideas.py`
- `tests/unit/test_surface_ideas_dedup.py`
- `tests/unit/test_compile_backup.py`
- `tests/unit/test_section_append.py`
- `tests/integration/test_compile_notes_shrink_guard.py`
- `tests/integration/test_surface_end_to_end.py`
- `tests/integration/test_surface_cross_reference.py` (D52: verify
  themes/concepts get bullets appended)

#### Modified files
- `praxis_core/schemas/task_types.py`:
  - Add `SURFACE_IDEAS` TaskType value
  - `MODEL_TIERS`: SURFACE_IDEAS → sonnet
  - `TASK_RESOURCE_KEYS`: SURFACE_IDEAS → "surface_ideas" (singleton)
- `praxis_core/schemas/payloads.py`:
  - Add `SurfaceIdeasPayload`
  - `CompileToWikiPayload`: add explicit `ticker: str | None` if not
    already (verify)
- `praxis_core/tasks/validators.py`:
  - Add validator for surface_ideas
  - Update `validate_compile_to_wiki`:
    - Drop INDEX.md requirement
    - Tighten citation check to `[[<analysis_path>]]` regex
    - Add shrink-guard: compare notes.md size against backup (if present)
- `praxis_core/db/models.py`:
  - Add `SurfacedIdea` ORM model
- `handlers/analyze_filing.py` (from Section A):
  - Add 3rd enqueue on trade_relevant: compile_to_wiki (D37)
- `handlers/compile_to_wiki.py`:
  - Pre-write backup via `stash_for_edit` (D38)
  - Drop INDEX.md directive from prompt; LOG only (D39)
  - (No post-write consolidate trigger — D40 dropped)
- `services/scheduler/main.py`:
  - `surface_ideas` every 30 min — 24/7 (OD2)
  - `refresh_index` bumped to every 15 min during market hours
  - `LOG.md` rotation check daily 03:00 ET
  - Morning digest ntfy push at 08:30 ET (reads surfaced_ideas table,
    composes single push)
- `services/mcp/server.py`:
  - `list_surfaced_ideas` tool
  - `surface_ideas_now` tool
- `handlers/prompts/` — organize per D28 + new prompts
- `praxis_core/vault/conventions.py`:
  - Add `surfaced_batch_path(vault, dt) -> Path` (for `_surfaced/<date>/ideas-<HHMM>.md`)
  - Add `surfaced_current_path(vault) -> Path`
  - Add `log_archive_path(vault, date) -> Path`
  - Add `backup_compile_path(vault, dt, ticker) -> Path`

---

### Implementation order (Section D)

1. `SurfacedIdea` schemas + migration
2. `stash_for_edit` helper + `backup_compile_path` convention
3. `append_to_section` helper (used by surface for D52 cross-refs)
4. compile_to_wiki hardening: pre-write backup, drop INDEX write, shrink-
   guard validator, citation regex tightening
5. compile_to_wiki trigger wiring in `analyze_filing` (3rd enqueue)
6. `surface_ideas` handler + prompt + validator + anomaly-cap enforcement
7. `surfaced_ideas` dedup logic
8. Cross-reference logic in surface_ideas: append bullets to each
   referenced theme/concept file (D52)
9. MCP tools: `list_surfaced_ideas`, `surface_ideas_now`
10. Scheduler entries: periodic surface 24/7, refresh_index bump, LOG
    rotation, morning digest at 08:30 ET
11. Unit tests: dedup, backup helper, section_append helper, validator
    tightening
12. Integration test: analyze → compile (with backup) → surface picks up
    the new analysis → idea emitted end-to-end → theme/concept files
    show new bullets
13. Manual smoke: feed two related 8-Ks (same theme, different tickers);
    verify surface detects cross_ticker_pattern AND theme file gets a
    bullet
14. Commit

---

### Open items — RESOLVED (Avyuk 2026-04-19 late eve)

- **OD1 — morning digest channel** → **ntfy (default proposal), unchanged**.
  Avyuk: "dwbi for now." Single consolidated ntfy push at 08:30 ET per D50.
- **OD2 — off-hours surface cadence** → **24/7, every 30 min always**.
  Avyuk: "offhours are the best time to ideate." D45 updated to drop the
  market-hours gate. Scheduled surface runs round-the-clock, every weekday
  and weekend.
- **OD3 — consolidate_notes auto/approval** → **DROP consolidation
  entirely for Monday**. Avyuk: "not concerned about the size of this
  wiki really." D40 removed. `consolidate_notes` handler, prompt, task
  type, validator — all dropped from this pass. Pre-write backup from D38
  stays (it's recovery protection, not consolidation).
- **OD4 — anomaly category** → **keep with cap of 1 per batch**.
  Enforced in the surface_ideas prompt (D47) AND in handler post-
  processing (drop any anomaly beyond index 0).
- **OD5 — cross-reference surfaced ideas into themes/concepts** →
  **YES, wire it**. New decision D52 added below.

### D52. Cross-reference surfaced ideas back into themes + concepts
When `surface_ideas` emits a batch, the handler — after writing the
`_surfaced/` batch file and persisting DB rows — iterates over each idea
and appends a dated bullet to every theme and concept file referenced in
the idea's `themes` list (and by analogous extension, in any
`concepts/<slug>.md` the idea's rationale references).

**Append format** — under a canonical `## Surfaced ideas` section
(create section if missing):

```markdown
## Surfaced ideas
- 2026-04-20T14:30:00-04:00 [[_surfaced/2026-04-20/ideas-1430]]
  **theme_intersection** · tickers: UUUU, CCJ — <one-line summary>
```

**Mechanics:**
- Parse the theme/concept file via `python-frontmatter`
- Ensure `## Surfaced ideas` section exists (append at end if not)
- Append new bullet line; write back via `atomic_write`
- Per-file: if the section already has a line with the same batch_handle
  + ticker set, skip (idempotency for re-runs)

**Race acceptance:** per D41's logic, append-to-existing-section is
append-only commutative. Two concurrent surface batches writing to the
same theme file can interleave without data loss. Flag for proper
theme/concept locking in the same FOLLOWUP entry as D41.

**Which files get appended to:**
- Every slug in `idea.themes` → `themes/<slug>.md` (if exists)
- Heuristic scan of `idea.rationale` for concept slug mentions — if a
  `concepts/<slug>.md` exists matching a keyword in the rationale, also
  append there. (Conservative: only if the slug appears as a clear token
  in the rationale, not fuzzy-matched.)
- Company notes are NOT auto-appended from surface (that's
  compile_to_wiki's job; avoid double-writing).

**New helper** `praxis_core/vault/section_append.py`:

```python
def append_to_section(
    path: Path,
    section_heading: str,   # e.g. "## Surfaced ideas"
    bullet_line: str,       # full bullet text, no leading "- "
    *,
    dedup_substring: str | None = None,
) -> bool:
    """Append a bullet under a named section, creating it if missing.
    Returns True if appended, False if dedup hit. Atomic via stash+rewrite."""
```

Reused from this surface use case; eventually compile_to_wiki should use
it too (cleaner than today's append-by-LLM-prompt approach). For Monday,
just use it in surface_ideas.

---

### Things NOT in this pass (deferred to FOLLOWUPS)

- **Proper multi-resource locking** (compile AND surface hold company +
  theme + concept locks simultaneously) — D41 and D52 accept the race
  for now. Real lock management is a future infra upgrade. The
  append-only commutative pattern covers the current use cases.
- **Consolidation of bloated notes.md** — D40 dropped per OD3. Revisit
  if/when notes grow unwieldy.
- **Email digest delivery** — ntfy only for Monday.
- **pgvector-backed semantic surfacing** — PLAN §16 mentions this;
  still deferred. Keyword/metadata matching is the heuristic we're
  using.
- **Scoring of surfaced ideas** — beyond urgency, we could score them
  (novelty, cross-ticker span, etc.). Skip for Monday.
- **Auto-promotion of recurring patterns to concepts/** — idea_type
  `concept_promotion` just surfaces the candidate; doesn't actually
  promote. Full promotion would need a human review step.
- **Archive of _surfaced/** — the `_surfaced/<date>/` dir grows over
  time. Clean up > 30d old batches in a later maintenance pass.

---

### Status (Section D)

- [x] D37-D52 decisions locked in (D40 removed, D52 added via OD-resolve)
- [x] Open items OD1-OD5 resolved
- [x] SurfacedIdea schemas + Alembic migration
- [x] stash_for_edit backup helper + vault conventions
- [x] append_to_section helper (for cross-refs)
- [x] compile_to_wiki hardening (backup, drop INDEX write, shrink-guard,
      citation regex tightening)
- [x] compile_to_wiki trigger wiring in analyze_filing
- [x] surface_ideas handler + prompt + validator + anomaly cap
- [x] surfaced_ideas dedup logic
- [x] Cross-reference logic: append to themes/concepts referenced by ideas
- [x] MCP tools (list_surfaced_ideas, surface_ideas_now)
- [x] Scheduler: surface 24/7 cadence, refresh_index bump, LOG rotation,
      morning digest
- [ ] Unit tests (dedup, backup, section_append, validator changes)
- [ ] Integration tests (compile shrink-guard, surface end-to-end,
      surface cross-reference)
- [ ] Manual smoke (two related 8-Ks → cross_ticker_pattern detection +
      theme file shows new bullet)
- [ ] Commit

---

## Section E — Migration: staging → production with Section A-D fit

Captured 2026-04-19 late evening. Avyuk flagged: the existing MIGRATION.md
in the repo was written before Sections A-D were decided; the
praxis-migrate CLI was built and run end-to-end (commit `de987fe`) but
produces a vault layout that doesn't fit our updated design. Needs heavy
audit and restructuring so the migrated wiki actually supports the
Section A analyze schema, Section B dive taxonomy, Section C MCP control
plane, and Section D surface-ideas flow.

**Goal for Monday:** cleanly cut over from autoresearch (v1) + copilot
workspace into a fresh `~/vault` (production) that:
- Uses the Section B dive-specialist directory conventions (one `dives/`
  folder per company, specialists named to match Section B's new task types)
- Has frontmatter on themes/concepts that idea-surfacing (Section D) can
  keyword-match against
- Has seeded `CLAUDE.md` schema doc at the vault root
- Has nothing left over that would trigger false positives in the ingest
  path (no stale `_raw/` or `_analyzed/` from old runs)
- Is ready for the system to start writing into at market open

**Current state of the migrate code:**
- `services/migrate/` exists, 1490 LOC across 7 files — covers plan/apply/
  validate/import-copilot-state
- Handles autoresearch vault rename + thesis merge + memo re-nest + source
  flatten + wikilink rewrite + frontmatter normalize
- Handles copilot workspace per-ticker memos + 3 analyst reports
  (`rigorous-financial-analyst.md`, `business-moat-analyst.md`,
  `macro-analyst.md`) + coordinator log + macro dedup
- **Gaps vs Section A-D** enumerated in D53-D61 below

---

### Gaps audit (what's wrong with the current migrator vs Sections A-D)

| Gap | Where | Section |
|---|---|---|
| Analyst outputs land at `companies/<TICKER>/analyst_reports/`, not `dives/` | `workspace_migrator.py:177` | B (D23 puts dives at `companies/<TICKER>/dives/<specialty>.md`) |
| Only 3 of copilot's 6+ specialist types are mapped | `workspace_migrator.py:103-107` (`_ANALYST_REPORTS` dict) | B (D19 taxonomy includes 6 specialists + custom) |
| Specialist slugs `rigorous-financial`, `business-moat` don't match v2 task types `dive_financial_rigorous`, `dive_business_moat` | `workspace_migrator.py:103-107` | B |
| No validation that theme/concept files have `tags:` frontmatter | `validate` CLI at `cli.py:145-199` | D (surface_ideas keyword-matches against tags) |
| Missing seed of `CLAUDE.md` schema doc at vault root | Nowhere | Vault convention |
| No pre-creation of `_surfaced/`, `_backups/compile/` areas (lazy creation is fine, but doc it) | N/A | D |
| S3 filing analysis import not implemented — MIGRATION.md §6 Phase 4 called for it, CLI doesn't have it | Missing | A (could fold historical copilot analyses into `_analyzed/` under new schema) |
| Copilot `events.yaml` (event calendar) not migrated | Missing | Cross-cutting |
| Copilot `draft_monitors.yaml` not migrated | Missing (and no monitors infra yet) | Deferred |
| Validator doesn't check ticker regex conformance (new ticker validator in `praxis_core/vault/conventions.py`) | `cli.py:145-199` | Vault |
| No `cutover` CLI command — `mv ~/vault-staging ~/vault` is manual | `cli.py` | Section E |
| Autoresearch wiki has `preliminary_decision`, `scores.tactical`, `scores.fundamental` fields that our schema didn't preserve intentionally — need to confirm they survived | `frontmatter.py` | Verify |
| INDEX.md is dropped on migration (per current logic — correctly, since refresh_index rebuilds it), but new vault needs it created empty | Partial | D39 change |
| No import of copilot's per-ticker `draft_monitors.yaml` — but we don't have a monitors infra yet, so drop | Intentional drop | Deferred |
| Wikilink rewriter doesn't account for new `_surfaced/` paths that will appear post-migration (inbound wikilinks only) | `wikilinks.py` | Benign — no source wikilinks point at `_surfaced/` |

---

### Design decisions (locked in)

### D53. Rename `analyst_reports/` to `dives/` — match Section B
In `workspace_migrator.py::_migrate_ticker`, change target paths from:
```
companies/<TICKER>/analyst_reports/<specialist_slug>.md
```
to:
```
companies/<TICKER>/dives/<specialist_slug>.md
```

And update slugs to match Section B's task-type stem naming (drop
`dive_` prefix in the filename since the directory already implies it's
a dive):

| Copilot source file | New v2 path |
|---|---|
| `rigorous-financial-analyst.md` | `dives/financial-rigorous.md` |
| `business-moat-analyst.md` | `dives/business-moat.md` |
| `industry-structure-cycle-analyst.md` | `dives/industry-structure.md` |
| `capital-allocation-analyst.md` | `dives/capital-allocation.md` |
| `geopolitical-risk-analyst.md` | `dives/geopolitical-risk.md` |
| `macro-analyst.md` | `dives/macro.md` |
| `supplement-reader-analyst.md` | `dives/supplement-reader.md` |

New Section B dive handlers write to the same `dives/<specialist>.md` path
on live runs. Migration-imported content overlays cleanly with runtime-
generated content. Older migrated dives get a `migrated_from:
copilot_workspace` frontmatter tag so a future `lint_vault` can
distinguish imported-baseline from system-generated.

### D54. Expand `_ANALYST_REPORTS` mapping to all 7 copilot specialists
Extend the `_ANALYST_REPORTS` dict in `workspace_migrator.py`:

```python
_ANALYST_REPORTS = {
    "rigorous-financial-analyst.md": "financial-rigorous",
    "business-moat-analyst.md": "business-moat",
    "industry-structure-cycle-analyst.md": "industry-structure",
    "capital-allocation-analyst.md": "capital-allocation",
    "geopolitical-risk-analyst.md": "geopolitical-risk",
    "macro-analyst.md": "macro",
    "supplement-reader-analyst.md": "supplement-reader",
}
```

All 7 specialists get migrated. Missing files per ticker simply don't
produce outputs — no error. (Copilot workspaces vary in completeness.)

### D55. Seed `CLAUDE.md` schema doc at target root
During `apply`, copy `vault_seed/CLAUDE.md` to `<target>/CLAUDE.md` as a
first-class step. The worker prompts already read this file via
`read_vault_schema()` (`handlers/_common.py:60`). If we skip seeding, the
LLM gets no schema context on live tasks.

Also copy:
- `vault_seed/INDEX.md` — empty but structurally valid (refresh_index
  will rewrite in 15 min cycles per D39)
- `vault_seed/LOG.md` — fresh empty LOG with a single header line

### D56. Validator — add frontmatter + tag checks
Extend `validate` CLI (`services/migrate/cli.py:145-199`) to:

1. **Validate frontmatter on every `.md` outside `_raw`/`_analyzed`:**
   - Must have `type:` — one of `memo | company_note | thesis | concept |
     theme | person | question | investigation | analyst_report | source |
     dive`. Log warnings for unknowns.
   - Must have `status:` when type is `thesis | investigation`.
   - Themes and concepts MUST have a `tags:` list with ≥1 entry.
     Surface_ideas depends on this to do keyword matching. (D24 + D47)

2. **Validate ticker format** — every `ticker:` frontmatter field is
   validated against `_TICKER_RE` from `praxis_core/vault/conventions.py`.
   Invalid tickers flagged.

3. **File-count sanity:** at least N companies, M themes, K concepts
   based on the source. If counts drop by >20% from source, flag. Catches
   catastrophic migration failures silently swallowing content.

4. **Dive-directory sanity:** for each `companies/<TICKER>/dives/`
   folder, every file basename matches the D54 specialist slug set OR
   starts with `custom-` (for migrated + runtime custom dives).

Output a structured `_migration_report.md` + `_migration_validation.md`
pair in the target root, plus human-readable summary to stdout.

### D57. New `cutover` CLI command
```bash
uv run python -m services.migrate.cli cutover \
    --staging ~/vault-staging \
    --production ~/vault \
    --require-validation-pass
```

Behavior:
1. Refuse if `--require-validation-pass` is set (default) and
   `<staging>/_migration_validation.md` doesn't exist OR contains any
   error-severity findings. Print the findings and exit.
2. Refuse if `<production>` exists and is non-empty without `--force`.
3. Rename atomic: `os.rename(staging, production)` if they're on the
   same filesystem; otherwise rsync-then-rmtree (and a warning that
   cross-filesystem cutover is slow).
4. Seed a `_cutover.log` at production root with timestamp + source
   staging path.
5. Emit a final checklist for the operator:
   - Update any symlinks if applicable
   - Restart dispatcher / pollers / MCP server
   - Run smoke test (`scripts/smoke.sh`)

### D58. Historical filing-analysis import — translate copilot → v2 schema
New CLI subcommand `import-copilot-filings`. Pulls (from S3, per the
AWS creds confirmed in OE1) copilot's historical
`praxis-copilot/data/raw/filings/{cik}/{accession}/analysis.json` files
and translates them into v2's `AnalysisResult` format, writing to
`_analyzed/filings/8-k/<accession>/analysis.json` in the target vault.

**Also covers press releases** per the parallel S3 layout:
`praxis-copilot/data/raw/press_releases/{source}/{ticker}/{release_id}/analysis.json`
→ `_analyzed/press_releases/{source}/{ticker}/{release_id}/analysis.json`.
Same near-identity schema translation since both use copilot's
`AnalysisResult` shape.

**Translation is near-identity** — copilot's schema (per `analyze/llm.py`):
```python
classification: Literal["BUY","SELL","NEUTRAL"]
magnitude: float
new_information: str
materiality: str
explanation: str
```

v2's `AnalysisResult` (Section A D4):
```python
classification: Literal["positive","negative","neutral"]
magnitude: float
new_information: str
materiality: str
explanation: str
```

Mapping: `BUY → positive`, `SELL → negative`, `NEUTRAL → neutral`.
Everything else passes through. Add `migrated_from: copilot_s3_filings`
marker.

**Scope:** import only for tickers that exist in the migrated
`companies/` directory, and only for filings from the last 180 days.
(Avyuk's MIGRATION.md §6 asked 90d — we're going slightly wider for
more historical context. Flag if you want tighter.)

**Skip** copilot's older pre-schema files where `classification` isn't
present. Mark them in a separate "migration_skipped" log.

Each translated analysis:
1. Writes `_analyzed/filings/8-k/<accession>/analysis.json` (v2 schema)
2. Writes a companion `_raw/filings/8-k/<accession>/meta.json` with a
   `migrated: true` marker and original `ingested_at` preserved
3. Does NOT write `filing.txt` — the raw HTML is not imported (storage
   concern). Flag in validator: migrated entries won't have `filing.txt`.
4. Inserts a `sources` Postgres row for dedup against future live
   ingest of the same accession.

### D59. Event calendar import — `events.yaml` → upcoming events
Copilot maintains `<copilot>/config/events.yaml` with known upcoming
catalysts per ticker (earnings dates, FDA decisions, etc.). Per copilot
research_prompt.py:528-538, this is how analysts anticipate catalysts.

New CLI subcommand `import-copilot-events`. Reads copilot's events.yaml
and writes to `<target>/config/events.yaml` in v2 vault OR a new
`events` table in Postgres.

**Decision:** write to Postgres (`events` table already exists for
system events; extend OR create a new `catalyst_events` table to
separate system events from investment catalysts). The surface_ideas
handler can read upcoming catalysts to enrich its prompt.

For Monday, the simpler path: copy `events.yaml` into
`<target>/config/events.yaml` and have any handler that needs it read
the file. Defer the DB-backed version to a follow-up since the file
format is already right.

### D60. Drop existing `_migration_report.md` from target before rerun
Current `apply` writes `_migration_report.md` to target root. If we
rerun into an existing staging (after fixing code per D53-D59), the old
report may mislead. Add a `--clean` flag that wipes the target dir
contents before applying. Combined with `--force` for safety.

### D77. Phase 0 — S3 audit BEFORE any migration code changes
Before touching `services/migrate/` or running any imports, audit what's
actually in the two accessible S3 buckets to ground every subsequent
decision. The audit produces a checked-in snapshot at
`docs/s3-audit-<YYYY-MM-DD>.md` that later steps reference.

**Audit commands (all read-only):**
```bash
# Bucket-level inventory
aws s3 ls s3://praxis-copilot/ --recursive --summarize | tail -5
aws s3 ls s3://8k-scanner-raw/ --recursive --summarize | tail -5

# Filings-prefix depth/count
aws s3 ls s3://praxis-copilot/data/raw/filings/ --recursive \
    | awk -F/ '{print $4}' | sort -u | wc -l   # unique CIKs
aws s3 ls s3://praxis-copilot/data/raw/filings/ --recursive \
    | grep analysis.json | wc -l               # analysis count

# Press releases — by source and count
aws s3 ls s3://praxis-copilot/data/raw/press_releases/ --recursive \
    | grep analysis.json | wc -l
aws s3 ls s3://praxis-copilot/data/raw/press_releases/ \
    | awk '{print $2}'                         # source subdirs

# Date range — newest + oldest analysis.json
aws s3 ls s3://praxis-copilot/data/raw/filings/ --recursive \
    | grep analysis.json | sort -k1,2 | head -1
aws s3 ls s3://praxis-copilot/data/raw/filings/ --recursive \
    | grep analysis.json | sort -k1,2 | tail -1

# Sample content — pull one analysis.json to eyeball schema
CIK=$(aws s3 ls s3://praxis-copilot/data/raw/filings/ | head -1 | awk '{print $NF}' | tr -d /)
ACC=$(aws s3 ls s3://praxis-copilot/data/raw/filings/$CIK/ | head -1 | awk '{print $NF}' | tr -d /)
aws s3 cp s3://praxis-copilot/data/raw/filings/$CIK/$ACC/analysis.json -
```

**What the audit report captures:**
- Total file count per bucket
- Per-prefix counts: filings, press_releases, 8k (legacy), state, config
- Unique CIK count for filings
- Date range of analysis.json artifacts (newest/oldest)
- Press release source breakdown (gnw, cnw, newsfile, other)
- One sample `analysis.json` pasted verbatim to confirm schema matches
  what D58 expects (`classification`, `magnitude`, `new_information`,
  `materiality`, `explanation`)
- Any surprises (unexpected prefixes, broken files, schema drift across
  time) that would affect import scope

**Why Phase 0 matters:** D58 + the CA PR import make assumptions about
schema + layout. If S3 drifted from what config.py says (e.g., older
filings have a different analysis.json shape), we want to know BEFORE
writing translation code, not after.

### D61. Rewrite MIGRATION.md
Current MIGRATION.md has open questions and discussion items that are
now decided by Sections A-D. Replace it with an authoritative, current
doc describing:
- Source inventory (autoresearch + copilot workspace + copilot S3 filings
  + copilot events)
- Target vault structure (matching Section A-D conventions)
- Rename map + merger logic (as-is, validated by the code)
- Cutover procedure (D57)
- Rollback (keep staging around for N days after cutover; autoresearch
  source is untouched — worst case, re-apply to fresh staging)
- Known data loss (raw filings from copilot S3 are not imported; noted)

Keep the "Open questions" section but mark resolved ones as such with
cross-references to Section A-D decisions.

---

### File-by-file change list (Section E)

#### New files
- `tests/unit/test_migrate_workspace_slugs.py` — verify D53 renaming +
  D54 full-taxonomy mapping
- `tests/unit/test_migrate_validator_frontmatter.py` — D56 frontmatter
  checks
- `tests/unit/test_migrate_copilot_filings.py` — D58 schema translation
- `tests/integration/test_migrate_end_to_end_vault_fit.py` — run migration
  against a fixture vault, verify output layout matches Section A-D
  expectations

#### Modified files
- `services/migrate/workspace_migrator.py`:
  - Expand `_ANALYST_REPORTS` per D54
  - Retarget paths from `analyst_reports/` to `dives/` per D53
  - Add `migrated_from: copilot_workspace` frontmatter marker (already
    partially present; ensure consistency)
  - Track per-specialist file counts in report for visibility
- `services/migrate/cli.py`:
  - Add `cutover` subcommand per D57
  - Add `import-copilot-filings` subcommand per D58
  - Add `import-copilot-events` subcommand per D59
  - Add `--clean` flag to `apply` per D60
  - Strengthen `validate` per D56 (frontmatter, tags, tickers, counts,
    dive-slug sanity)
- `services/migrate/vault_migrator.py`:
  - Add seed step per D55 (copy `vault_seed/CLAUDE.md`, `INDEX.md`,
    `LOG.md` to target)
  - Ensure `companies/<TICKER>/dives/` dir is pre-created for known
    tickers (so D23 writes don't hit mkdir race with other processes)
- `services/migrate/frontmatter.py`:
  - Preserve `preliminary_decision`, `scores.tactical`, `scores.fundamental`
    fields from autoresearch (audit: confirm they survive normalization —
    per MIGRATION.md §6 they're meant to)
- `MIGRATION.md`:
  - Full rewrite per D61
  - Replace "discussion items" with "resolved decisions ↔ Section X.Y"
- `vault_seed/CLAUDE.md`:
  - Review + update to match Section A-D conventions:
    - `analysis.json` is the sole analyze output (Section A D1)
    - `dives/<specialty>.md` convention (Section B D53)
    - Surfaced ideas live under `_surfaced/` (Section D)
    - Filing PR sources also valid (`_raw/press_releases/`, Section A)
- `vault_seed/INDEX.md`:
  - Minimal frame; `refresh_index` task (every 15 min per D39) rebuilds
- `vault_seed/LOG.md`:
  - Fresh empty LOG with header

---

### Implementation order (Section E)

0. **Phase 0 — S3 audit (D77).** Run the read-only audit commands,
   write `docs/s3-audit-<YYYY-MM-DD>.md`, confirm schema alignment
   with D58 expectations. If the sample analysis.json drifts from the
   expected shape, update D58 translation before proceeding.
1. Rewrite `vault_seed/CLAUDE.md` to match Section A-D conventions
2. Update `workspace_migrator.py` per D53 + D54 (dives/ rename, full
   specialist taxonomy)
3. Update `vault_migrator.py` per D55 (seed CLAUDE.md, INDEX.md, LOG.md)
4. Strengthen `validate` CLI per D56 (frontmatter, tags, tickers, counts,
   dive-slug sanity)
5. Add `--clean` flag to `apply` per D60
6. Add `cutover` CLI per D57
7. Add `import-copilot-filings` per D58 — **reads from S3 directly via
   `boto3`**, covers both `data/raw/filings/` and
   `data/raw/press_releases/` prefixes. Default bucket: `praxis-copilot`.
   Also probes `8k-scanner-raw/` for any overlap and logs coverage.
8. Add `import-copilot-events` per D59 (events.yaml file copy)
9. Unit + integration tests per the test manifest above — add a mocked-
   S3 test for `import-copilot-filings` using `moto` or equivalent
10. Rewrite `MIGRATION.md` per D61
11. Dry-run: `praxis-migrate plan --autoresearch-vault ~/dev/praxis-autoresearch/vault
    --copilot-workspace ~/dev/praxis-copilot/workspace --target ~/vault-staging`
    and review report
12. Apply: `praxis-migrate apply --clean ... --target ~/vault-staging`
13. Validate: `praxis-migrate validate --target ~/vault-staging` — must
    pass with 0 errors
14. Import copilot filings + PRs: `praxis-migrate import-copilot-filings
    --target ~/vault-staging --since-days 180` (reads S3 per D58)
15. Import copilot events: `praxis-migrate import-copilot-events
    --target ~/vault-staging`
16. Re-validate after imports
17. **Human review of staging** — Avyuk walks through spot-checks:
    - `companies/NVDA/notes.md` reads cleanly
    - `companies/CLMT/dives/financial-rigorous.md` present and sensible
    - `themes/strait-of-hormuz.md` has tags
    - Imported `_analyzed/filings/8-k/<acc>/analysis.json` parses as
      v2 `AnalysisResult`
    - Imported `_analyzed/press_releases/<source>/<ticker>/<id>/analysis.json`
      parses
    - A few recent memos look right
    - No stray `_migration_report.md` leaking wikilinks
18. Cutover: `praxis-migrate cutover --staging ~/vault-staging
    --production ~/vault`
19. Run `scripts/smoke.sh` end-to-end against the live vault
20. Commit

---

### Open items

### OE1 — RESOLVED: S3 access confirmed, scope expanded
AWS creds present on this host, verified:
- `aws-cli/2.34.32` at `~/.local/bin/aws`
- Caller: `arn:aws:iam::703222328817:user/avyukd`
- Two buckets accessible: **`8k-scanner-raw`** and **`praxis-copilot`**

Copilot's S3 layout (from `src/modules/events/eight_k_scanner/config.py`):

| Prefix | Content |
|---|---|
| `praxis-copilot/data/raw/8k/` | Legacy older 8-K storage |
| `praxis-copilot/data/raw/filings/{cik}/{accession}/` | Current canonical 8-K filings (extracted.json, analysis.json, screening.json, index.json) |
| `praxis-copilot/data/raw/press_releases/{source}/{ticker}/{release_id}/` | PR storage (both US + CA) |
| `praxis-copilot/data/raw/us-pr/` | US PR (alternate) |
| `praxis-copilot/data/raw/ca-pr/` | CA PR (alternate) |
| `praxis-copilot/data/state/*.json` | Poller last-seen state |
| `praxis-copilot/config/*.yaml` | Monitor configs, universe, etc. |
| `8k-scanner-raw/` | Raw 8-K HTML dumps (original ingest, pre-extraction) |

The S3 audit is **Phase 0** of Section E implementation (runs BEFORE
any migration code changes) — see D77 below and implementation-order
step 0.

### OE2 — events.yaml destination
Keep as YAML file at `<vault>/config/events.yaml` for Monday (simpler),
or migrate now to a new Postgres `catalyst_events` table?

Propose: **file for Monday, DB migration later.** Less plumbing;
surface_ideas can read the file if it wants catalyst context.

### OE3 — drop vs preserve autoresearch-only fields
`preliminary_decision`, `scores.tactical`, `scores.fundamental` are
autoresearch fields that v2's synthesize_memo will start producing too
(Section B). Keeping migrated values might mix "human-curated tactical
score" with "LLM-generated tactical score" in the same field.

Propose: preserve with a `_legacy_` prefix on migration (e.g.,
`_legacy_scores.tactical`) so live system doesn't overwrite them, and a
future consolidation pass can reconcile. Low-priority — flag for human
review during D61 MIGRATION.md rewrite.

---

### Things NOT in this pass (deferred to FOLLOWUPS)

- **Postgres-backed `catalyst_events` table** — file-based for Monday (OE2)
- **Semantic dedup on imported filings** (vs file-basename dedup) —
  import might create near-duplicates if an accession is referenced by
  multiple copilot runs. Accept for Monday.
- **Copilot `draft_monitors.yaml` import** — no monitors infra in v2
  yet. Drop; revisit when monitors are built.
- **Raw filing HTML import from S3** — storage concern, skip. If a
  migrated analysis references the raw, the worker can re-fetch from SEC
  on demand.
- **Per-ticker portfolio/watchlist inference from copilot** — copilot
  has a portfolio YAML; migrating it informs `boost_ticker` auto-pins.
  Out of scope.

---

### Status (Section E)

- [x] D53-D61 decisions locked in
- [ ] Open items OE1-OE3 resolved (need Avyuk call if non-default wanted;
      defaults are in the "Propose:" bullets above)
- [x] `vault_seed/CLAUDE.md` rewritten for Section A-D conventions
- [x] workspace_migrator: dives/ rename + full 7-specialist mapping
- [x] vault_migrator: seed CLAUDE.md / INDEX.md / LOG.md during apply
- [x] Validator strengthened (frontmatter, tags, tickers, counts,
      dive-slug sanity)
- [ ] `--clean` flag on apply
- [x] `cutover` CLI command
- [x] `import-copilot-filings` CLI command
- [x] `import-copilot-events` CLI command
- [ ] Unit tests (workspace slugs, validator frontmatter, copilot-filings
      translation)
- [ ] Integration test (end-to-end migration fit against fixture vault)
- [x] `MIGRATION.md` rewritten per D61
- [x] Apply to `~/vault-staging` (merged directly into live vault via cutover)
- [x] Historical filings imported (1476 filings + 3127 press releases from copilot S3)
- [x] Events imported (12 daily events from copilot S3)
- [ ] Human review of staging (Avyuk spot-checks company notes / dives /
      themes / memos)
- [x] Cutover `~/vault-staging` → `~/vault`
- [x] Smoke test green against live `~/vault`
- [ ] Commit

---

## Section F — Continuous audit/test/iterate loop

Captured 2026-04-19 late evening. After implementation of Sections A-E,
rapid-fire changes to 2000+ LOC across schemas, handlers, pollers,
migrators, and MCP tools create real risk of:
- Cross-section contract drift (Section A changes a field shape, Section
  C reads the old shape)
- Dead code resurrected (imports of deleted modules, stale validator
  entries)
- Test gaps (new code that has no test at all)
- Prompt drift (one specialist prompt references a task type that got
  renamed)
- Migration mismatches (Section E writes a path Section B doesn't read)

A Claude Code `/loop` cron runs every 10 min throughout the overnight
build to catch these as they happen, not post-hoc.

**Scope directive:** this section is the *meta-work* that enforces
quality across Sections A-E. It runs concurrently with them. It is NOT
a substitute for the per-section tests defined in A-E — it complements
them by catching cross-cutting issues those narrower tests miss.

---

### Design decisions (locked in)

### D62. `/loop` cron mechanism
Use Claude Code's `/loop` skill in CronCreate-backed mode (from the
`loop` and `schedule` skills in the CLI harness). Fire every 10 min.
Invoke with a specific audit prompt (see D64). The cron lives in
`~/.claude/crons/` or equivalent; schedule via `/schedule` skill.

```
/schedule every 10m \
  audit-iterate --prompt "@docs/audit-prompt.md"
```

Where `docs/audit-prompt.md` is a checked-in file containing the D64
prompt. This makes the audit logic versionable — future edits to the
audit checklist are diffable.

### D63. Termination rule
After **3 consecutive iterations** with no actionable findings, the
audit agent deletes its own cron. "No actionable findings" is defined as:
- Zero new entries in `AUDIT_FINDINGS.md` since the previous iteration
- `pytest tests/ -q` exits 0
- `ruff check` exits 0
- `pyright praxis_core handlers services` exits 0 (or matches a known
  baseline count of pre-existing non-blocking warnings)

Each iteration logs a status line to `AUDIT_LOG.md` so you can see the
history at a glance: `2026-04-20T02:30:00 — iteration 4 — 0 findings
— PASS`.

### D64. Audit prompt (checked in at `docs/audit-prompt.md`)
The prompt the cron executes each firing:

```
You are the continuous-audit agent for praxis-v2. Run on a 10-minute
cadence during the overnight implementation window leading into the
2026-04-20 ship.

Your job on each firing:

1. Read OVERNIGHT.md's Status checkboxes across Sections A-E to understand
   what has/hasn't been implemented.

2. Read git log + git diff since the last audit iteration's tag
   (look for tag `audit-iter-N` on main).

3. Audit the changes against these specific failure modes:

   a. CROSS-SECTION CONTRACT DRIFT
      - Schema fields written by one section vs read by another
      - TaskType enum values referenced everywhere (especially after Section
        B's rename: dive_business → dive_business_moat; dive_financials
        → dive_financial_rigorous)
      - Payload shape changes not propagated to validators

   b. DEAD CODE / ORPHAN IMPORTS
      - grep for `depends_on` (should be gone per D26)
      - grep for `pause_investigation` / `resume_investigation` (should be
        gone per D32)
      - grep for `dive_business` / `dive_moat` / `dive_financials`
        (renames per D19)
      - grep for `analysis.md` (should only appear in comments /
        backup paths per D1)
      - grep for `AnalysisSignals` (replaced by AnalysisResult per D4)

   c. TEST GAPS
      - New handler files without corresponding tests
      - New validators without test coverage
      - New MCP tools without a sanity test
      - For each Section's `Status` checklist — if an item is marked [x]
        but no corresponding test exists, flag it

   d. PROMPT DRIFT
      - Grep every SYSTEM_PROMPT constant for mentions of task types or
        file paths
      - Verify mentioned task types still exist in TaskType enum
      - Verify mentioned file paths still match vault conventions
      - Flag prompts that reference fields we've deleted (e.g. any
        remaining "linked_themes" / "thesis_impacts" from old
        AnalysisSignals)

   e. MIGRATION DRIFT
      - services/migrate/* should write to paths Sections A-D READ from
      - workspace_migrator's dive slug mapping (Section E D54) should
        match Section B's specialist filenames
      - CLAUDE.md schema doc seeded by migration (D55) should reflect
        current conventions

   f. SANITY CHECKS (run as commands)
      - pytest tests/ -q (must exit 0)
      - ruff check (must exit 0)
      - pyright praxis_core handlers services (must match baseline)
      - git status clean (no accidentally-staged junk)

4. For each finding, decide:

   TRIVIAL AUTO-FIX (apply directly):
     - Missing import in a file that just needs it
     - Stale reference to a renamed enum value
     - Missing test file scaffold for a new handler
     - Ruff-fixable lint issues
     - Typo in a prompt that matches a renamed thing
     - Dead import where the target is obviously gone

   REQUIRES HUMAN REVIEW (append to AUDIT_FINDINGS.md):
     - Anything touching a prompt's content (not just path/name refs)
     - Anything touching schema field semantics
     - Anything touching task flow (enqueue sites, validator logic,
       worker lifecycle)
     - Anything that would change migration behavior
     - Any test that newly fails after a fix
     - Anything requiring a design decision

5. After acting, update AUDIT_LOG.md with one line:
   `<et-iso> — iteration <N> — <findings-count> findings — <fixed-count>
   auto-fixed — <reported-count> reported — <test-status>`

6. Check termination criterion (D63). If met, delete the cron with
   `/unschedule audit-iterate` and write a final summary to
   AUDIT_LOG.md.

Rules for the audit agent:
- Never commit: leave changes staged/unstaged for the human to review
  (the human is also implementing Sections A-E, so dirty working tree
  is expected)
- If `pytest` is currently failing because Avyuk is mid-edit, wait one
  iteration before reporting — give the main stream a chance to finish
- Always read OVERNIGHT.md fresh each iteration; decisions may have
  been updated mid-build
- Don't audit committed-but-unapplied sections (e.g., if Section D is
  still all unchecked, skip drift checks between D and other sections)
- Keep AUDIT_FINDINGS.md under 100 lines by collapsing older already-
  addressed findings into a `## Resolved` section at the bottom
```

### D65. Auto-fix vs report split — the safety boundary
The D64 prompt defines the split explicitly. Principle: **prompts,
schemas, and task flow are high-blast-radius.** Touching them wrong can
propagate silently across N handlers. Auto-fix never touches these.

Import/reference/typo/lint-level fixes are low-blast-radius and get
auto-applied. The diff is small, readable on `git diff` by the human
operator whenever they look.

`AUDIT_FINDINGS.md` is the medium-trust channel: findings the agent
couldn't safely auto-fix, grouped by category, with a proposed action for
each. Human acts on them.

### D66. What "interesting" means — explicit list
Interesting findings (ANY of these is actionable):
- A test failing that was passing
- A cross-section contract drift (field name / type / enum mismatch)
- A dead reference to a deleted name
- A prompt referencing a renamed thing
- A migration path mismatch
- A new file without a test
- A lint error
- A TODO in code that blocks a Section A-E checkbox

Non-interesting (do NOT count for termination):
- WIP code in the middle of a feature (if `git diff` has unstaged
  partial work, don't report incomplete imports inside it)
- Pre-existing warnings that match a known baseline
- Stylistic preferences the team hasn't established

### D67. Artifacts
New files created by the audit loop:
- `AUDIT_FINDINGS.md` — live findings the human should act on. Grouped
  by severity (critical / warning / info). Older resolved findings
  collapsed into `## Resolved` at bottom.
- `AUDIT_LOG.md` — append-only log, one line per iteration. Machine-
  readable timestamps + counts. Useful for "did the audit find anything
  overnight?" triage.
- `docs/audit-prompt.md` — the D64 prompt, checked in so it's versioned.

---

### File-by-file change list (Section F)

#### New files
- `docs/audit-prompt.md` — the D64 prompt (full text checked in)
- `AUDIT_FINDINGS.md` — starts empty, populated by the loop
- `AUDIT_LOG.md` — starts with a header line, appended each iteration

#### Modified files
- `.gitignore` — do NOT gitignore `AUDIT_*` files; they should be visible
  to the human operator (and committed if useful for historical context).
  Actually — they ARE temporary working docs; probably ignore the log
  (noisy) but track FINDINGS.md. Flagged as OF2 open item.

---

### Implementation order (Section F)

Section F is unusual in that its "implementation" is mostly a one-time
setup, then it runs on its own alongside the other sections:

1. Write `docs/audit-prompt.md` with the D64 prompt text
2. Create empty `AUDIT_FINDINGS.md` and `AUDIT_LOG.md` with headers
3. Schedule the cron: `/schedule every 10m audit-iterate` with the
   prompt file as argument
4. Tag the current commit `audit-iter-0` so the first iteration has a
   starting point for `git diff`
5. Run one iteration manually to verify it produces sane output
6. Let it run continuously during Section A-E implementation
7. When termination criterion (D63) is met, cron self-deletes and
   writes final summary

---

### Open items

### OF1 — auto-fix aggressiveness on prompt-adjacent changes
The D65 split says prompts get reported, not auto-fixed. Edge case:
a prompt has a stale path like `_analyzed/filings/8-k/...` that got
renamed. The "fix" is a pure find/replace. Should the loop:

(a) Apply trivial path-only fixes in prompts too (they're not semantic
    changes)
(b) Report all prompt changes regardless, preserving the safety boundary

Lean: **(b) report-only for anything inside a SYSTEM_PROMPT constant.**
Err on the side of human review when it comes to prompts. Flag if you
want to relax.

### OF2 — track AUDIT_LOG.md in git?
If we commit the log, future-you can see "what did the agent do
overnight?" months from now. Cost: noisy commits.
Propose: track AUDIT_FINDINGS.md (value retained post-cleanup), ignore
AUDIT_LOG.md (operational noise).

### OF3 — should the audit loop run sanity tests against the live DB?
Integration tests require Postgres. If we're rapidly modifying schemas
during Sections A-E, running migrations + integration tests every 10
min could thrash the DB.
Propose: **unit tests only in the audit loop** (fast, no DB). Integration
tests run once per Section commit as part of that section's own CI.

---

### Status (Section F)

- [x] D62-D67 decisions locked in
- [ ] Open items OF1-OF3 resolved (leaning defaults proposed)
- [x] `docs/audit-prompt.md` written (D64 prompt)
- [x] `AUDIT_FINDINGS.md` + `AUDIT_LOG.md` initialized
- [x] Cron scheduled
- [x] First manual iteration verified sane
- [x] Loop runs through Section A-E implementation
- [x] Termination criterion met → self-delete (fired iter 6, 3 consecutive clean)
- [ ] Final `AUDIT_FINDINGS.md` reviewed by Avyuk (may trigger cleanup
      work)

---

## Section G — Setup, deployment, and morning observability loop

Captured 2026-04-19 late evening. After Sections A-F are implementable,
we need to actually stand the system up on real hardware with real
credentials and real external endpoints, then keep it running reliably
through Monday open. Three parts:

- **Part 1 — Infra setup** (Postgres, secrets, systemd, Claude CLI auth)
- **Part 2 — Deployment target + IRL smoke**
- **Part 3 — Pre-market + trading-hours observability /loop with
  tiered self-healing**

This section is the difference between "code written" and "system live."

---

### Design decisions (locked in)

### D68. Deployment target — RESOLVED: this WSL box IS the Ryzen
Confirmed 2026-04-19 late eve: the WSL box we're on (via Claude Code) is
the Ryzen. Single host. OG1 collapsed — no split-target decision needed.

Implications vs PLAN.md §14 (which assumed Ryzen + Air as separate
machines):
- **Drop Tailscale** — not needed for MVP; everything binds localhost.
  MCP server, dashboard, Postgres — all local. Tailscale would only
  matter if we later want phone/off-device access to the dashboard or
  MCP. Deferred.
- **Drop Syncthing** — not needed for MVP; no second machine to sync
  the vault to. Backups (if we want them) handled via restic.
- **Drop Caddy** — optional reverse proxy; for MVP bind dashboard
  directly on `127.0.0.1:8080` or `0.0.0.0:8080` (local-only since no
  Tailscale routing). Defer Caddy to a later pass if/when dashboard
  needs TLS termination or external routing.
- **systemd confirmed available** — `systemd=true` in `/etc/wsl.conf`,
  PID 1 is systemd, `systemctl` functional. Use D71 unit files as-is;
  no overmind/Procfile fallback needed.
- **Claude CLI logged in** — `~/.local/bin/claude` 2.1.114, Max
  subscription authenticated. OG2 also resolved.

### D69. Postgres setup
On chosen host:
```bash
sudo apt install postgresql-16 postgresql-contrib
sudo -u postgres createuser --pwprompt praxis
sudo -u postgres createdb -O praxis praxis
sudo -u postgres psql -c "CREATE EXTENSION IF NOT EXISTS pgcrypto;" -d praxis
```

Then from repo:
```bash
export DATABASE_URL=postgresql+asyncpg://praxis:<pw>@localhost:5432/praxis
export ALEMBIC_DATABASE_URL=postgresql://praxis:<pw>@localhost:5432/praxis
alembic upgrade head
```

On WSL fallback: same steps except `systemctl` → `service postgresql
start` and `.env` absolute paths.

### D70. Secrets inventory
Inventory of secrets needed, where they live, who needs them. All live
in `.env` (local) or systemd `EnvironmentFile=` (production); NONE in
code or checked-in config.

| Secret | Used by | Source |
|---|---|---|
| `DATABASE_URL` | All services | Generated per D69 |
| `ALEMBIC_DATABASE_URL` | Alembic | Generated per D69 |
| `SEC_USER_AGENT` | edgar_8k, press pollers | Avyuk's email per SEC's contact policy |
| `NTFY_BASE_URL` | notify handler, scheduler | ntfy.sh (default) or self-hosted |
| `NTFY_SIGNAL_TOPIC` | notify handler | User-chosen topic name (anything unique) |
| `NTFY_ALERT_TOPIC` | scheduler | User-chosen topic name |
| `ANTHROPIC_API_KEY` | API invoker only (NOT cli) | Anthropic console; only if `PRAXIS_INVOKER=api` |
| Claude Max login | CLI invoker (spawned by worker) | Interactive login via `claude` command once |
| `RESTIC_REPOSITORY`, `RESTIC_PASSWORD_FILE` | syncer | AWS S3 bucket + local password file |
| Tailscale auth | Cross-host connectivity | `tailscale up --authkey=...` |

**Pre-deploy checklist** that must complete before services start:
- [x] Postgres up, role+DB created, `alembic upgrade head` succeeds
- [x] `.env` populated with all of the above
- [x] `claude` CLI logged in with Max subscription (interactive, one-
      time; run `claude` once and follow prompts)
- [ ] ntfy topics subscribed on phone (install ntfy app, subscribe to
      both signal + alert topics)
- [x] SEC_USER_AGENT contains a real email per SEC policy
- [ ] Tailscale up if using Tailscale-routed MCP/dashboard

### D71. systemd units (Track 1 — Ryzen)
Units already scaffolded in `infra/systemd/` per the repo. Section G
audit + extensions:
- `praxis-dispatcher.service`
- `praxis-scheduler.service`
- `praxis-mcp.service`
- `praxis-dashboard.service`
- `praxis-syncer.service`
- `praxis-poller-edgar-8k.service`
- `praxis-poller-inbox.service`
- **NEW per Section A:** `praxis-poller-press-us.service`,
  `praxis-poller-press-ca.service`
- **NEW per Section B D25:** `praxis-mcp-fundamentals.service` (the
  fundamentals MCP server runs as its own process, co-located with
  the main MCP server or separate — default: separate for isolation)

All with `Restart=always`, `RestartSec=10`, `StartLimitBurst=5`,
`WatchdogSec=60`, `StandardOutput=journal`. Templates already in
`infra/systemd/`; audit and ensure the two new pollers + fundamentals
MCP service have their unit files.

### D72. [REMOVED per D68 resolve] WSL fallback — no longer needed
The two-track setup collapsed when OG1 resolved to single-host
(this WSL box IS the Ryzen). Procfile/overmind fallback unused; systemd
handles all service supervision. Procfile stays checked in for local
dev convenience, but not the production path.

### D72b. Third-party service inventory (single-host)
Sorted by: what I install locally / what runs external / what we drop.

**Install locally on this host:**
| Package | Purpose | Install method |
|---|---|---|
| `postgresql-16` + `postgresql-contrib` | Queue + state | `sudo apt install` |
| `uv` | Python package manager | Official installer script → `~/.local/bin/uv` |
| `restic` (optional) | S3 backups | `sudo apt install` only if we want backups for Monday |

**External services (no local install):**
| Service | Purpose | Setup |
|---|---|---|
| ntfy.sh | Phone push notifications | Install ntfy app on phone; subscribe to 2 topics |
| yfinance (Yahoo Finance) | Market cap + fundamentals | Python lib; anonymous HTTP |
| SEC EDGAR | 8-K / 10-Q / 10-K feed | Public HTTP; needs real email in SEC_USER_AGENT |
| GlobeNewswire RSS | US + CA press releases | Public HTTP scrape |
| CNW / Newsfile | CA press releases | Public HTTP scrape |
| Anthropic Claude | LLM invocation | Already auth'd via `claude` CLI Max subscription |

**Dropped from PLAN.md §14 (single-host makes them unnecessary for MVP):**
- Tailscale (no cross-host networking)
- Syncthing (no second machine to sync to)
- Caddy (dashboard can bind localhost directly)

### D73. IRL smoke test sequence
After infra is up and services are running, execute this sequence to
confirm end-to-end liveness:

1. **Dispatcher heartbeat:** `psql -c "SELECT * FROM heartbeats;"` —
   all components reporting within 60s.
2. **Enqueue a cheap probe task** via `scripts/smoke.sh` — verifies
   dispatch → worker → validate → mark-success lifecycle.
3. **Rate-limit state:** should be `clear`. `psql -c "SELECT * FROM
   rate_limit_state;"`
4. **Trigger a real 8-K ingest:** either wait for one to arrive from the
   EDGAR feed (during market hours) OR force one by running
   `uv run python -m services.pollers.edgar_8k --once` (add `--once`
   flag to the poller for this purpose; default loop behavior otherwise).
5. **Observe the filing flow:**
   - `_raw/filings/8-k/<acc>/filing.txt` written
   - `analyze_filing` task in `tasks` table (status=queued→running→success)
   - `_analyzed/filings/8-k/<acc>/screen.json` written
   - If non-negative: `analysis.json` written
   - If trade-relevant: `notify` + `orchestrate_dive` + `compile_to_wiki`
     all enqueued
   - ntfy push received on phone
6. **Let a dive complete** (5-10 min) and verify:
   - Six specialist files appear in `companies/<TICKER>/dives/`
   - `synthesize_memo` produces the memo
   - investigation status transitions to `resolved`
7. **Trigger a surface run manually:** call `surface_ideas_now()` MCP
   tool; verify a batch shows up in `_surfaced/<date>/`.
8. **Test the MCP control plane:** call `list_investigations()`,
   `cancel_investigation(handle)` on a spare investigation, verify
   cascade works and running task actually stops.

Each step gets a checkbox; all green = production-ready.

### D74. Morning observability + self-heal loop
A `/loop` cron running every 15 min during active hours, hourly
overnight, that checks system health and attempts tiered self-heal on
detected issues. Written as a Claude Code invocation so it can reason
about findings (unlike a static shell cron).

**Schedule:**
- 04:30 ET: first firing (30 min before morning filings start arriving)
- Every 15 min from 05:00-16:00 ET
- Every 60 min from 16:00-04:30 ET (overnight, lower cadence)

**Checks on each firing:**

1. **Heartbeat freshness** — every registered component has a heartbeat
   within its expected interval (dispatcher 2 min, pollers 5 min,
   scheduler 5 min, MCP 10 min).
2. **Dispatcher progress** — `tasks` table: was any task transitioned
   to `running` or `success` in the last 15 min? (During market hours
   with EDGAR polling.) If no transitions for 15+ min during market
   hours → stall suspected.
3. **EDGAR poller** — last `filing_ingested` or `filing_rejected` event
   within the last 5 min during market hours. If silent, poller
   may be stuck.
4. **Rate limit state** — not stuck in `limited` for >30 min.
5. **Dead-letter queue** — any increase in dead_letter_tasks since last
   iteration? If yes: surface the error pattern.
6. **Pool saturation** — if all 4 workers busy continuously for >20 min
   without any completing, probable hung worker.
7. **Disk/vault checks** — disk free >5GB, vault reachable, no orphan
   tempfiles (`.*.tmp.*` older than 10 min in vault).
8. **Surface-ideas freshness** — a new batch should have appeared in
   the last ~35 min (30-min cadence + slack). If not, surface scheduler
   may be broken.

**Self-heal tiers:**

**Tier 1 (soft heal — always auto-apply):**
- Clear stale leases: `UPDATE tasks SET lease_holder=NULL,
  lease_expires_at=NULL WHERE status='running' AND lease_expires_at <
  now() - interval '10 minutes'`. Worker that died holding a lease has
  its task picked up on next dispatch.
- Clean orphan tempfiles in vault older than 10 min.
- Reset `rate_limit_state` to `probing` if stuck in `limited` past its
  `limited_until_ts` (shouldn't happen — dispatcher does this — but
  belt + suspenders).

**Tier 2 (hard heal — auto-apply after Tier 1 fails for 2 iterations):**
- `systemctl restart praxis-<service>` for services whose heartbeat is
  stale.
- On WSL: `overmind restart <service>`.
- Log the restart as an event.

**Tier 3 (escalate — notify and stop):**
- If Tier 2 did not recover the component within 2 more iterations
  (total ~1 hour of degraded): fire a `high` ntfy push to the `alert`
  topic with the failing component + last error + last 20 lines of
  journal.

### D75. Observability loop prompt (checked in at `docs/observability-prompt.md`)
```
You are the operational observability + self-heal agent for praxis-v2.
Fire schedule per D74. Your job:

1. Query current system state:
   - SELECT component, last_heartbeat, status FROM heartbeats
   - SELECT COUNT(*), status FROM tasks GROUP BY status
   - SELECT * FROM rate_limit_state
   - SELECT COUNT(*) FROM dead_letter_tasks WHERE failed_at > now() - interval '1 hour'
   - SELECT MAX(ts) FROM events WHERE component='pollers.edgar_8k' AND event_type IN ('filing_ingested','filing_rejected')
   - Find recent _surfaced/ batches (mtime within 35 min)
   - Disk usage

2. For each red signal (per D74 checks a-h), decide Tier 1/2/3 action.

3. Apply Tier 1 actions directly. Log to `OBSERVABILITY_LOG.md`.

4. Check for 2-iteration consecutive-failure: if this is the 2nd+
   iteration a specific component has been red after Tier 1, escalate
   to Tier 2 (restart).

5. Tier 3 firing requires: Tier 2 tried and failed twice, or critical
   data integrity issue (Postgres unreachable, vault disk full).

6. Always append a status line to OBSERVABILITY_LOG.md:
   `<et-iso> — tier-<N>-actions: <count> — red-signals: <list> —
   services-up: <count>/<total>`.

7. During market hours (08:00-16:00 ET), cadence is 15 min. Off-hours,
   60 min. Check `now_et()` to determine which mode.

8. Soft heals you're authorized to execute without asking:
   - Clearing stale leases
   - Cleaning orphan tempfiles in vault
   - Resetting rate_limit_state if observably stuck past its window

9. Hard heals you're authorized to execute after the 2-iteration rule:
   - systemctl restart praxis-<service> (Track 1)
   - overmind restart <service> (Track 2)

10. Never: restart the whole machine, touch the DB schema, modify code,
    delete vault content, modify Postgres data beyond the specific
    lease-clear and state-reset queries above.

11. Escalation ntfy topic is `NTFY_ALERT_TOPIC` from .env. Post with
    priority "urgent" and include the diagnostic snapshot.
```

### D76. Deletion criterion for the observability loop
Unlike Section F's audit loop (which self-deletes when the codebase
stabilizes), Section G's observability loop runs **indefinitely.** It's
a production safety net, not a one-time quality gate. Don't design it
for self-deletion.

The user can manually `/unschedule observability` if they want to stop
it (e.g., planned maintenance). Otherwise it keeps running.

---

### File-by-file change list (Section G)

#### New files
- `docs/observability-prompt.md` — the D75 prompt
- `OBSERVABILITY_LOG.md` — append-only operational log (gitignored —
  noisy; value is real-time, not historical)
- `infra/systemd/praxis-poller-press-us.service`
- `infra/systemd/praxis-poller-press-ca.service`
- `infra/systemd/praxis-mcp-fundamentals.service`
- `scripts/preflight.sh` — runs the D70 pre-deploy checklist as a
  script (verifies .env populated, Postgres reachable, claude CLI
  logged in, ntfy topic reachable)

#### Modified files
- `infra/Procfile`:
  - Add press_us, press_ca, mcp-funds lines per D72
- `infra/bootstrap.sh`:
  - Audit + ensure Postgres setup per D69 is covered
  - Add the new systemd units per D71
- `infra/deploy.sh`:
  - Ensure it restarts ALL praxis-*.service units after git pull
- `services/pollers/edgar_8k.py`, `services/pollers/press_us.py`,
  `services/pollers/press_ca.py`:
  - Add `--once` CLI flag for one-shot polling (used by smoke test
    D73 step 4 + observability poller-health checks)
- `scripts/smoke.sh`:
  - Extend to cover the D73 sequence (currently only covers refresh_index)

---

### Implementation order (Section G)

OG1/OG2 resolved (single host, CLI authenticated). Remaining sequence:

1. **Install `uv`** — official installer:
   `curl -LsSf https://astral.sh/uv/install.sh | sh` →
   `~/.local/bin/uv` on PATH
2. **Install Postgres 16:**
   `sudo apt install -y postgresql-16 postgresql-contrib`
3. **Postgres role + DB + extensions** per D69
   (create praxis role + DB, `CREATE EXTENSION pgcrypto`)
4. **`uv sync`** in repo root — installs all Python deps including
   `beautifulsoup4`, `lxml` (Section A), `anthropic`, `mcp`, etc.
5. **`alembic upgrade head`** — applies all migrations through the new
   ones from Sections B (0003-0005), D (0006)
6. **Populate `.env`** per D70:
   - DATABASE_URL / ALEMBIC_DATABASE_URL
   - SEC_USER_AGENT (your real email)
   - NTFY_BASE_URL (default ntfy.sh), NTFY_SIGNAL_TOPIC,
     NTFY_ALERT_TOPIC (you pick unique names)
   - VAULT_ROOT, INBOX_ROOT, CLAUDE_SESSIONS_ROOT, LOG_DIR
   - PRAXIS_INVOKER=cli
7. **Subscribe ntfy topics on phone** — install ntfy app, subscribe to
   `<your signal topic>` and `<your alert topic>`
8. **Test ntfy end-to-end:**
   `curl -d "test from praxis" https://ntfy.sh/<your-topic>` →
   push lands on phone
9. **Write `scripts/preflight.sh`** (D70) — green-checks all of the
   above before services start
10. **Install systemd unit files** per D71 — copy from `infra/systemd/`
    to `/etc/systemd/system/`, `sudo systemctl daemon-reload`,
    `sudo systemctl enable praxis-*.service`
11. **Start services:** `sudo systemctl start praxis-*.service`
12. **Verify heartbeats:** `psql praxis -c "SELECT * FROM heartbeats;"`
    — all components reporting within 60s
13. **Execute D73 smoke sequence end-to-end** (8-step checklist,
    ingest → analyze → notify → dive → surface)
14. **Write `docs/observability-prompt.md`** (D75 content)
15. **Schedule observability /loop** per D74 (05:00 ET first firing,
    15-min cadence during market hours, 60-min overnight)
16. **First manual iteration of observability loop** — verify sane
    output, no unintended self-heal actions fire
17. **Let observability loop run continuously** through ship morning
18. **Commit** (infra unit files, scripts, prompts, .env.example
    updates; NEVER commit `.env` itself)

---

### Open items

### OG1 — RESOLVED: this WSL box IS the Ryzen
Single host. No split-target concern. See D68 update.

### OG2 — RESOLVED: Claude CLI 2.1.114 logged in on this host
Max subscription active. CLI invoker will inherit auth cleanly.

### OG3 — observability loop's authorization to restart services
D74 Tier 2 says the agent can `systemctl restart praxis-<service>`.
This requires passwordless sudo for the `praxis` user on those
specific units (sudoers config). Are you OK with that level of
automation authority?

Alternative: Tier 2 produces a "restart recommended" ntfy, and you
authorize manually via `/tool systemctl-restart <service>` MCP tool.
More friction; less risk.

Propose: **passwordless sudo for the specific systemctl restart
commands on praxis-*.service.** You've already accepted the "relentless
pursuit of signal, human prunes after the fact" principle from Section
A — same philosophy for self-healing. If a hard-heal is wrong, fix the
code; don't gate production recovery on human availability.

### OG4 — morning cadence — is 05:00 ET early enough?
8-Ks start arriving around 06:00 ET (some companies file pre-open).
Market opens 09:30 ET. Our ingest needs to be proven-working by 08:00
ET so we're confident before the pre-market filing burst.

05:00 ET gives 3h of observation before 08:00 ET — enough slack for
the observability loop to catch + heal any overnight drift.

Propose: **05:00 ET first firing stands.** Flag if you want earlier
(risky — you're asleep) or later (less slack).

### OG5 — disk/filesystem monitoring threshold
D74 step g says "disk free >5GB." Tune based on your actual disk size.
If the deploy host has 200GB free, 5GB threshold is too low — you
want escalation at ~20% capacity remaining, not "emergency only."

Propose: threshold = max(5GB, 10% of total capacity). Adjust after
observing actual fill rate over the first week.

### OG6 — RESOLVED: fade backups for now
Avyuk: "we can fade the backups. not really worried about that for rn."
No restic install. No `praxis-syncer.service` enablement for Monday.
Postgres is the durable state + vault lives on local disk. If paranoid
before ship, a manual `tar czf vault-snapshot.tar.gz ~/vault` takes 30
seconds. Revisit backups post-Monday.

Implications for the install sequence:
- Drop `sudo apt install restic` from step 1
- Drop `RESTIC_*` env vars from `.env` population
- `praxis-syncer.service` unit file can stay on disk but stays
  `disabled` (don't start it)

---

### Things NOT in this pass (deferred to FOLLOWUPS)

- **Multi-host deployment** — Ryzen + Air as redundant pair. Single
  host for Monday.
- **Backup verification** — restic backups configured (D70) but the
  "test a restore" step isn't part of Monday. Run a restore test
  manually within the first week.
- **Performance profiling** — once the system is live and under real
  load, a pass to identify bottlenecks in the dispatcher tick, worker
  startup, etc. Out of scope.
- **Advanced self-heal** — reconciling partial investigations where
  the financial_rigorous dive succeeded but orchestrator was
  interrupted before enqueueing specialists. Needs heuristics; defer.
- **Dashboard enrichment** — add the Section F/G observability signals
  as dashboard panels so Avyuk can spot-check without reading logs.
  Nice-to-have; not blocking.

---

### Status (Section G)

- [x] D68-D76 decisions locked in
- [x] OG1 resolved — single host (this WSL box IS the Ryzen)
- [x] OG2 confirmed — Claude CLI 2.1.114 authenticated locally
- [ ] OG3 resolved — self-heal authorization level (leaning passwordless
      sudo for restart-only)
- [ ] OG4 confirmed — 05:00 ET first firing
- [ ] OG5 tuned — disk threshold
- [x] OG6 resolved — fade backups for now; no restic/S3 setup
- [x] `.env` populated per D70
- [x] Postgres + alembic per D69
- [x] Claude CLI logged in on deploy host
- [ ] ntfy topics subscribed on phone
- [ ] `scripts/preflight.sh` written + green
- [ ] `scripts/smoke.sh` extended for D73 sequence
- [ ] New systemd units (press_us, press_ca, mcp-funds) for Track 1
- [ ] Updated Procfile for Track 2
- [ ] Pollers have `--once` flag
- [ ] Services started (systemd or overmind)
- [ ] D73 smoke sequence executed + all checkpoints green
- [ ] `docs/observability-prompt.md` written
- [ ] Observability loop scheduled per D74
- [x] First manual iteration verified sane
- [ ] Commit (infra unit files, scripts, prompts)

---

# System-wide open-items summary (all sections)

All opens consolidated for quick review before Avyuk says go. Answered
defaults shown; override any before implementation.

| # | Section | Question | Status |
|---|---|---|---|
| OF1 | F | Prompt-adjacent auto-fix aggressiveness | Default: report-only |
| OF2 | F | Track AUDIT_LOG.md in git? | Default: ignore log; track findings |
| OF3 | F | Audit loop runs integration tests? | Default: unit only |
| OG1 | G | Deploy target | **RESOLVED** — single host (this WSL box = Ryzen) |
| OG2 | G | Claude CLI login | **RESOLVED** — 2.1.114 authenticated |
| OG3 | G | Self-heal restart authority (passwordless sudo)? | Lean yes, flag if not |
| OG4 | G | 05:00 ET first observability firing | Stands |
| OG5 | G | Disk-free threshold | max(5GB, 10% of capacity) |
| OG6 | G | restic/S3 backups for Monday? | **RESOLVED** — fade backups |
