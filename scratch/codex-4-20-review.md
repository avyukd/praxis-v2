# Praxis V2 Comprehensive Audit (2026-04-20)

## Scope + Method
- Repository-wide static sweep across `praxis_core/`, `services/`, `handlers/`, `tests/`, docs and ops surfaces.
- Executed checks:
  - `uv run pytest -q` -> `230 passed, 23 skipped`
  - `uv run pytest -q -rs` (to inspect skip reasons)
  - `uv run ruff check .` (fails)
  - `uv run pyright` (fails)
- Focus areas:
  - Production-breaking bugs
  - Missing/partial features that appear intended by code/comments/docs
  - Test coverage gaps that allow regressions through

## Executive Summary
The codebase is in solid shape for core happy paths, but there are several high-impact reliability gaps:
1. Scheduler jobs for `refresh_backlinks` and `ticker_index` are currently broken at enqueue time.
2. Theme investigations can be opened but do not schedule work (feature appears half-implemented).
3. Dispatcher worker-id handling is brittle and depends on direct mutation of private state.
4. CI-quality gates are not clean (`pyright` + `ruff` both fail), with multiple type-safety and lint issues in hot paths.
5. Test suite is green, but it misses key coverage for scheduler + payload-map completeness.

---

## Findings (by severity)

### Critical

1. Scheduler background jobs silently fail for `refresh_backlinks` and `ticker_index`.
- Evidence:
  - Jobs are enqueued in [services/scheduler/main.py](/home/avyuk/dev/praxis-v2/services/scheduler/main.py:92) and [services/scheduler/main.py](/home/avyuk/dev/praxis-v2/services/scheduler/main.py:105).
  - Payload models exist as classes in [praxis_core/schemas/payloads.py](/home/avyuk/dev/praxis-v2/praxis_core/schemas/payloads.py:123) and [praxis_core/schemas/payloads.py](/home/avyuk/dev/praxis-v2/praxis_core/schemas/payloads.py:127).
  - But both are missing from `PAYLOAD_MODELS` map in [praxis_core/schemas/payloads.py](/home/avyuk/dev/praxis-v2/praxis_core/schemas/payloads.py:154), and `enqueue_task` hard-validates through this map.
- Impact:
  - These scheduler jobs fail every run and never enter the queue.
  - Graph hygiene and orphan ticker indexing do not happen, even though they appear enabled.
- Why it slipped:
  - No tests asserting `TaskType -> payload model` completeness.
  - Scheduler swallows per-job exceptions and only logs warnings.

### High

2. `open_investigation(theme=...)` creates an investigation but enqueues no work.
- Evidence:
  - Function promises to "Open a new investigation and enqueue the orchestrator" in [services/mcp/server.py](/home/avyuk/dev/praxis-v2/services/mcp/server.py:452).
  - Enqueue branch is gated on `if ticker:` only in [services/mcp/server.py](/home/avyuk/dev/praxis-v2/services/mcp/server.py:476).
- Impact:
  - Theme investigations become inert records (`status=active`) without task execution.
  - Observer workflow appears successful but does not progress.
- Missing feature:
  - Theme-scope orchestration path (or explicit rejection until supported).

3. Dispatcher task claim/submission relies on mutating private worker sequence state.
- Evidence:
  - Direct writes to `pool._worker_seq` in [services/dispatcher/main.py](/home/avyuk/dev/praxis-v2/services/dispatcher/main.py:86) and [services/dispatcher/main.py](/home/avyuk/dev/praxis-v2/services/dispatcher/main.py:90).
  - Worker IDs are allocated in multiple places (`claim`, `execute_task`, `submit`) making correctness non-obvious.
- Impact:
  - Fragile lease-holder semantics; easy to break during refactor.
  - Hard-to-debug race/identity issues around heartbeats and lease extension.
- Risk:
  - Not currently failing tests, but this is a structural reliability hazard.

4. MCP server has a real symbol-shadowing/lint failure in constitution tool wiring.
- Evidence:
  - Imports `append_principle` at [services/mcp/server.py](/home/avyuk/dev/praxis-v2/services/mcp/server.py:29) and redefines `append_principle` tool at [services/mcp/server.py](/home/avyuk/dev/praxis-v2/services/mcp/server.py:202).
  - `ruff` flags `F811`.
- Impact:
  - Increases risk of accidental call-site confusion and maintenance bugs.
  - CI/static quality currently red.

### Medium

5. Static type health is materially degraded in production paths.
- Evidence: `uv run pyright` reports 17 errors, including:
  - `handlers/surface_ideas.py` metadata typing assumptions (e.g. `.lower()` on `object`) around [handlers/surface_ideas.py](/home/avyuk/dev/praxis-v2/handlers/surface_ideas.py:130).
  - `praxis_core/newswire/cnw.py` BeautifulSoup attribute typing around href handling in [praxis_core/newswire/cnw.py](/home/avyuk/dev/praxis-v2/praxis_core/newswire/cnw.py:73).
  - `services/dispatcher/worker.py` coroutine typing at [services/dispatcher/worker.py](/home/avyuk/dev/praxis-v2/services/dispatcher/worker.py:135).
  - `services/mcp/fundamentals/tools.py` return type mismatches across several functions.
  - `praxis_core/vault/followups.py` `.lower()` on untyped metadata in [praxis_core/vault/followups.py](/home/avyuk/dev/praxis-v2/praxis_core/vault/followups.py:114).
- Impact:
  - Harder to trust refactors and harder to catch real runtime bugs before deploy.

6. Scheduler resilience model hides persistent job breakage.
- Evidence:
  - Job loop catches exceptions and logs warnings in [services/scheduler/main.py](/home/avyuk/dev/praxis-v2/services/scheduler/main.py:232), then continues.
- Impact:
  - Permanent misconfigurations can persist unnoticed except logs.
  - Dead-man checks won’t necessarily detect single-job failures.
- Missing feature:
  - Per-job failure counters/escalation/heartbeat status fields.

7. Test suite gives a strong green signal while skipping all DB integration tests in current env.
- Evidence:
  - 23 skipped tests, most gated by `PRAXIS_TEST_DATABASE_URL` and live integration env flags.
- Impact:
  - Local confidence can be overstated for lifecycle/claim/concurrency behavior.
- Gap:
  - Need an easy default integration test path in dev/CI for DB-backed critical flows.

8. `surface_ideas` prompt module imports an unused symbol.
- Evidence:
  - `SYSTEM_PROMPT` imported but unused in [handlers/surface_ideas.py](/home/avyuk/dev/praxis-v2/handlers/surface_ideas.py:31), flagged by `ruff`.
- Impact:
  - Not runtime-critical, but signal of code drift and prompt plumbing inconsistency.

---

## Missing / Partial Features

1. Theme investigation execution path is incomplete.
- Current behavior: theme investigations can be created but no tasks are queued.
- Expected by API contract/comments: investigation opening should trigger work.

2. Payload registry completeness checks are missing.
- The project uses explicit payload classes + `TaskType` enum but lacks a guard that every enqueueable task has a payload model.
- This directly caused the scheduler breakage.

3. Operational alerting for job-level scheduler failures is missing.
- There is component heartbeat alerting, but no durable "job X has failed N consecutive runs" alert.

4. Static quality gate enforcement is missing in routine workflow.
- `ruff` + `pyright` currently fail, yet code is merge/deploy-able.
- Suggests no blocking pre-merge gate for these checks.

---

## Recommended Fix Order (next session)

1. Fix payload-map omissions (`refresh_backlinks`, `ticker_index`) and add a unit test that fails if any enqueueable `TaskType` is absent from `PAYLOAD_MODELS`.
2. Decide theme investigation behavior:
   - either implement theme orchestration path, or
   - reject `theme` in `open_investigation` with explicit `not implemented`.
3. Refactor dispatcher worker-id/claim flow to remove direct `_worker_seq` mutation and make worker identity single-source.
4. Clear static check failures (`ruff` + `pyright`) in production modules.
5. Add scheduler job-health telemetry (consecutive failures + last error per job, with alert threshold).

---

## Verification Artifacts
- Tests: `uv run pytest -q` -> `230 passed, 23 skipped`
- Lint: `uv run ruff check .` -> fails (10 issues, including F811 + F401)
- Type-check: `uv run pyright` -> fails (17 errors, 3 warnings)

---

## Round 2: Next 20 Issues (non-duplicate)

### 1) `allowed_tools=[]` is ignored, enabling tools unexpectedly (High)
- Evidence:
  - Fallback uses `allowed_tools or BASE_ALLOWED_TOOLS` in [handlers/_common.py](/home/avyuk/dev/praxis-v2/handlers/_common.py:53).
  - Callers pass empty lists expecting no tools in [handlers/analyze_filing.py](/home/avyuk/dev/praxis-v2/handlers/analyze_filing.py:202) and [handlers/_dive_base.py](/home/avyuk/dev/praxis-v2/handlers/_dive_base.py:284).
- Impact:
  - "No-tools" calls still get baseline file/shell tools.
  - Prompt/validator assumptions about tool restrictions are violated.

### 2) Resource-key contract is partially broken for several task types (High)
- Evidence:
  - Declared keys include `cleanup`, `surface_ideas`, `wiki_mgmt` in [praxis_core/schemas/task_types.py](/home/avyuk/dev/praxis-v2/praxis_core/schemas/task_types.py:102).
  - `_resource_key_for` only handles `company`, `investigation`, `index|lint|journal` in [praxis_core/tasks/enqueue.py](/home/avyuk/dev/praxis-v2/praxis_core/tasks/enqueue.py:14).
- Impact:
  - Intended serialization for these tasks silently does not happen (`resource_key=None`).

### 3) Manual ingest path compiles into `companies/UNKNOWN` (High)
- Evidence:
  - Manual ingest enqueues compile without ticker in [services/mcp/server.py](/home/avyuk/dev/praxis-v2/services/mcp/server.py:826) and [services/pollers/inbox_watcher.py](/home/avyuk/dev/praxis-v2/services/pollers/inbox_watcher.py:97).
  - Compile handler substitutes missing ticker with `"UNKNOWN"` in [handlers/compile_to_wiki.py](/home/avyuk/dev/praxis-v2/handlers/compile_to_wiki.py:26).
- Impact:
  - Unattributed sources pollute a synthetic `companies/UNKNOWN` node instead of proper theme/concept/manual namespace.

### 4) Downstream dedup keys for press releases are not source-scoped (High)
- Evidence:
  - Dedup for downstream tasks uses only `release_id` in [services/pollers/press_ca.py](/home/avyuk/dev/praxis-v2/services/pollers/press_ca.py:206) and [services/pollers/press_us.py](/home/avyuk/dev/praxis-v2/services/pollers/press_us.py:165).
  - Analyze handler downstream dedups also use `form_type + item_id` in [handlers/analyze_filing.py](/home/avyuk/dev/praxis-v2/handlers/analyze_filing.py:346).
- Impact:
  - If two sources share an ID shape, notify/compile/analyze can be incorrectly deduped and dropped.

### 5) Canadian market-cap enrichment is not propagated to analysis cache lookups (Medium)
- Evidence:
  - CA poller fetches mcap under `.TO/.V` symbol in [services/pollers/press_ca.py](/home/avyuk/dev/praxis-v2/services/pollers/press_ca.py:126).
  - Analyze stage looks up cache using unsuffixed ticker in [handlers/analyze_filing.py](/home/avyuk/dev/praxis-v2/handlers/analyze_filing.py:178).
- Impact:
  - Press-release analysis often sees mcap as unknown even when poller already resolved it.

### 6) `notify` performs blocking network I/O inside async worker (Medium)
- Evidence:
  - Synchronous `httpx.Client` in [handlers/notify.py](/home/avyuk/dev/praxis-v2/handlers/notify.py:22).
  - Called directly from async handler in [handlers/notify.py](/home/avyuk/dev/praxis-v2/handlers/notify.py:54).
- Impact:
  - Blocks event loop thread during retries/timeouts; harms worker concurrency under ntfy latency.

### 7) `synthesize_memo` can retry forever on skeleton dives (High)
- Evidence:
  - Returns `transient=True` whenever any dive file exists but is `<1500` bytes in [handlers/synthesize_memo.py](/home/avyuk/dev/praxis-v2/handlers/synthesize_memo.py:289).
- Impact:
  - If a dive is stuck/crashed and leaves a skeleton, memo task can loop indefinitely without consuming attempts.

### 8) `orchestrate_dive` infers `initiated_by` via substring in `thesis_handle` (Medium)
- Evidence:
  - Prompt hardcodes `initiated_by: observer` if `"observer" in thesis_handle` at [handlers/orchestrate_dive.py](/home/avyuk/dev/praxis-v2/handlers/orchestrate_dive.py:117).
- Impact:
  - Provenance metadata can be wrong, undermining auditability.

### 9) `dive_custom` is still intentionally dropped (feature gap) (Medium)
- Evidence:
  - Explicitly removed from plan in [handlers/orchestrate_dive.py](/home/avyuk/dev/praxis-v2/handlers/orchestrate_dive.py:236).
- Impact:
  - Custom-specialty branch is present in taxonomy but not executable end-to-end.

### 10) Memo quality gate ignores custom dives entirely (Medium)
- Evidence:
  - `SPECIALTIES` list excludes `custom` in [handlers/synthesize_memo.py](/home/avyuk/dev/praxis-v2/handlers/synthesize_memo.py:25).
- Impact:
  - Investigation quality checks cannot account for work done in custom specialist outputs.

### 11) `refresh_index` does not include `companies/*/index.md` nodes (Medium)
- Evidence:
  - Company collection scans only `*/notes.md` in [handlers/refresh_index.py](/home/avyuk/dev/praxis-v2/handlers/refresh_index.py:22).
- Impact:
  - `ticker_index` stubs are omitted from global index unless notes.md exists.

### 12) `cleanup_sessions` bypasses payload schema validation (Medium)
- Evidence:
  - Direct `int(ctx.payload.get(...))` in [handlers/cleanup_sessions.py](/home/avyuk/dev/praxis-v2/handlers/cleanup_sessions.py:21), no `CleanupSessionsPayload.model_validate`.
- Impact:
  - Malformed payloads can raise and fail task rather than cleanly validating.

### 13) Daily journal task window is based on `started_at` only (Medium)
- Evidence:
  - Query filters on `Task.started_at` in [handlers/generate_daily_journal.py](/home/avyuk/dev/praxis-v2/handlers/generate_daily_journal.py:30).
- Impact:
  - Tasks that started before midnight ET but finished on the day are excluded from the day summary.

### 14) Inbox frontmatter is vulnerable to filename-based YAML injection (Medium)
- Evidence:
  - Raw filename inserted unescaped into frontmatter in [services/pollers/inbox_watcher.py](/home/avyuk/dev/praxis-v2/services/pollers/inbox_watcher.py:84).
- Impact:
  - Newlines/colon patterns in filenames can corrupt metadata structure.

### 15) `search_vault` is fully synchronous and unbounded over vault size (Medium)
- Evidence:
  - Walks entire vault and reads files synchronously in async tool [services/mcp/server.py](/home/avyuk/dev/praxis-v2/services/mcp/server.py:83).
- Impact:
  - MCP responsiveness degrades on large vaults; can starve other requests.

### 16) Event emission is out-of-transaction with business writes (Medium)
- Evidence:
  - `emit_event` always opens its own `session_scope` in [praxis_core/observability/events.py](/home/avyuk/dev/praxis-v2/praxis_core/observability/events.py:17).
- Impact:
  - Events can be committed even when surrounding operation later fails/rolls back, or vice versa.

### 17) Dashboard client renders unsanitized data via `innerHTML` (High, security)
- Evidence:
  - Multiple renderers interpolate payload strings directly into HTML template strings in [services/dashboard/app.py](/home/avyuk/dev/praxis-v2/services/dashboard/app.py:320) and assign via `innerHTML` in [services/dashboard/app.py](/home/avyuk/dev/praxis-v2/services/dashboard/app.py:371).
- Impact:
  - Stored XSS risk if task errors/events/signals contain HTML/script-like content.

### 18) `syncer` retention cadence is tied to tick count, not elapsed time (Low/Medium)
- Evidence:
  - `forget` runs every 24 ticks in [services/syncer/main.py](/home/avyuk/dev/praxis-v2/services/syncer/main.py:121).
- Impact:
  - If backup interval changes from hourly, prune cadence drifts from intended daily behavior.

### 19) `file_to_vault` path check is lexical, not resolved-path safe (High, security)
- Evidence:
  - Uses `p.relative_to(vault_root)` without `resolve()` in [services/mcp/server.py](/home/avyuk/dev/praxis-v2/services/mcp/server.py:775).
- Impact:
  - Symlinked paths inside vault can potentially escape intended write boundary.

### 20) Test coverage gap across critical operational paths (High, quality)
- Evidence:
  - `rg` search over `tests/` found no direct references for `press_ca`, `press_us`, `inbox_watcher`, `syncer`, `search_vault`, `file_to_vault`, `ingest_source`, `requeue_dead_letter`.
- Impact:
  - High-risk production control paths can regress without automated detection.
