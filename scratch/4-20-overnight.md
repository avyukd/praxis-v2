# 4-20 Overnight Plan

## Overnight tally (final — 2026-04-21 ~03:00 ET)

Shipped:
- **Phase 0 vault memory** (e1e3723) — search_vault upgraded to
  two-stage ranked
- **Phases 1-6 research engine** (73064ea) — `research_query`
  entrypoint, 6 new task types, 4 new MCP tools, screening bridges
  to existing dives
- **A.1 synthesize_memo skeleton-retry cap** (0bc6593) —
  ship-critical for tomorrow's pipelines
- **A.2 + A.3 + A.5 cleanup** (36f2c1f) — F811 shadow, symlink
  safety, unused import
- **+53 unit tests** — suite now 332 green (from 279)

Explicitly skipped:
- A.4 UNKNOWN ticker routing — manual-ingest only, not on the
  Monday pipeline critical path
- A.2.7 dashboard XSS — off ship path, post-Monday
- F.8 Phase 7 refresh loop — post-Monday

System state check before wrap-up still blocked on Anthropic
upstream throttle; probe mechanism post-fix keeps cycling
correctly, queue waits without burning cost.

---



Tight ship-day TODO. Re-verified against HEAD on 2026-04-21 —
Codex's audit (`scratch/codex-4-20-review.md`) was produced earlier
and a lot of it is already fixed. Only items that ACTUALLY still exist
in the current tree are below.

Legend:
- `[ ]` open
- `[x]` done
- `[-]` deferred post-Monday (not stale — intentionally skipped)

Status:
- 🔴 ship blocker
- 🟠 real bug
- 🟡 cleanup
- 🔒 security

---

## Section A — Real work, re-verified

### A.1 `[x]` `synthesize_memo` has no wall-clock cap on transient retries 🟠
**Shipped** commit `0bc6593`. SKELETON_WALLCLOCK_CAP_S=4h; skeletons
older than cap fall through to degraded memo instead of transient loop.
5 unit tests added.
**Most important correctness fix.**

- **Why**: `release_task` backoff fix + `transient=True` means
  cooperative waits don't burn attempts. Good. But if a dive truly
  crashes and leaves a <1500-byte skeleton forever, memo task loops
  indefinitely. Real risk under upstream RL pressure.
- **Scope**: `handlers/synthesize_memo.py` around line 289
  (`skeleton_specialties` check).
- **Approach**:
  1. For each skeleton dive, compute age via `p.stat().st_mtime`.
  2. If OLDEST skeleton is >4h old, stop waiting — emit a degraded
     memo that notes `skeleton_specialties` as known gaps + return
     `ok=True`.
  3. Otherwise keep returning `transient=True`.
  4. Emit event `synthesize_memo.skeleton_timeout` when the degraded
     path fires.
- **Verify**:
  - Unit test: skeleton mtime 5h ago → `ok=True` path.
  - Unit test: skeleton mtime 1h ago → `ok=False, transient=True`.
- **Risk**: 4h is arbitrary, make it a module-level constant.
- **Est**: 45 min.

### A.2 `[x]` MCP `append_principle` shadows imported symbol (F811) 🟠
**Shipped** commit `36f2c1f`. Renamed MCP tool to `add_principle`;
module-level import of `append_principle` from
`praxis_core.vault.constitution` is now called directly.
- **Why**: `services/mcp/server.py:28` imports `append_principle` from
  `praxis_core.vault.constitution`; line 201 redefines it as MCP tool.
  Line 220 works around with `from ... import ... as _append`. Bad
  smell + fails ruff.
- **Scope**: `services/mcp/server.py` — rename tool only.
- **Approach**:
  1. Rename MCP tool function `append_principle` → `add_principle`.
  2. Remove the inner `_append` alias; call module-level
     `append_principle` directly.
  3. Update the observer `claude.md`/docs if they reference the tool.
- **Verify**:
  - `.venv/bin/python -m ruff check services/mcp/server.py` clean of F811.
  - Tool shows as `add_principle` in registered names.
- **Risk**: Breaks anyone invoking `mcp__praxis__append_principle(...)`
  (zero-cost for Avyuk since day-zero feature).
- **Est**: 15 min.

### A.3 `[x]` `file_to_vault` no symlink-resolved path safety 🔒
**Shipped** commit `36f2c1f`. Parent `resolve()` + `relative_to`
check now catches symlink escapes.
- **Why**: `relative_to(vault_root)` is lexical — a symlink inside the
  vault pointing outside lets a caller write arbitrary paths. Low
  exposure (only Avyuk) but cheap.
- **Scope**: `services/mcp/server.py:file_to_vault` (around line 775
  per audit; confirm at fix time).
- **Approach**:
  1. `dest = Path(dest_path)`
  2. `parent = dest.parent.resolve()`; `vault = vault_root.resolve()`
  3. `parent.relative_to(vault)` — raises if outside.
  4. Same treatment for any other write sinks in that file.
- **Verify**: Unit test creating a tmp vault with a symlink escape,
  assert the tool rejects.
- **Est**: 30 min.

### A.4 `[ ]` UNKNOWN ticker fallback in `compile_to_wiki` 🟠
- **Why**: `handlers/compile_to_wiki.py:57` uses `ticker or "UNKNOWN"`
  when manual ingest (inbox_watcher / `file_to_vault`) forwards
  without a ticker. Creates a synthetic `companies/UNKNOWN/` node.
- **Scope**: `services/mcp/server.py` + `services/pollers/inbox_watcher.py`
  (the two enqueue sites), OR `handlers/compile_to_wiki.py` (the
  fallback).
- **Approach** (chose (b)):
  - (b) When ticker is missing at compile time, skip `compile_to_wiki`
    and route the raw file to `_inbox_manual/<YYYY-MM-DD>/`. No more
    `companies/UNKNOWN`.
  - Change the two enqueue callsites — don't enqueue compile without
    ticker; instead write to the manual-ingest namespace directly.
- **Verify**: Integration test — enqueue `file_to_vault` without
  ticker, assert `_inbox_manual/` file exists and no
  `companies/UNKNOWN` dir is created.
- **Risk**: If any existing vault refs `companies/UNKNOWN`, we'd
  break the link. Grep first.
- **Est**: 45 min.

### A.5 `[x]` Drop unused `SYSTEM_PROMPT` import in surface_ideas 🟡
**Shipped** commit `36f2c1f`.
- **Why**: Orphan after the modal refactor (mode-specific prompts
  replaced single `SYSTEM_PROMPT`).
- **Scope**: One line in `handlers/surface_ideas.py:31`.
- **Approach**: Remove the import. Leave
  `handlers/prompts/surface_ideas.py` on disk unless confirmed
  no-other-callers (per CLAUDE.md "don't remove pre-existing dead
  code unless asked").
- **Verify**: `ruff check handlers/surface_ideas.py` clean.
- **Est**: 2 min.

---

## Section B — Ship order

1. **A.5** unused import (2 min) — trivial warmup.
2. **A.2** MCP rename (15 min).
3. **A.3** file_to_vault resolve() (30 min).
4. **A.1** synthesize_memo wall-clock cap (45 min) — primary fix.
5. **A.4** UNKNOWN ticker routing (45 min).

Total: ~2h15m. After A.1 we're correctness-ready on the known risks.

---

## Section C — Post-Monday backlog (swept 2026-04-21)

Triaged 2026-04-21 after Monday open. Shipped what shipped;
deliberate skips noted.

- `[x]` **Resource-key contract missing for surface_ideas /
      cleanup_sessions / refresh_backlinks / ticker_index** (Round2 #2).
      Already fixed in earlier commits (TASK_RESOURCE_KEYS now has
      singleton keys for all four: `cleanup` / `surface_ideas` /
      `wiki_mgmt`).
- `[x]` **Dashboard `innerHTML` XSS** (Round2 #17). Shipped in 04160dc:
      added `esc()` helper, applied to every user-controlled field
      (last_error, investigation handle/hypothesis, signal title,
      event payload, DL error).
- `[x]` **`notify` uses sync `httpx.Client` in async handler**
      (Round2 #6). Already fixed in 1402439.
- `[x]` **`refresh_index` misses `index.md` stubs** (Round2 #11).
      Shipped in 04160dc: now includes `<dir>/index.md` and
      `<dir>/notes.md` stubs alongside top-level *.md.
- `[x]` **`cleanup_sessions` skips Pydantic validation** (Round2 #12).
      Shipped in 04160dc.
- `[x]` **`generate_daily_journal` window on `started_at` only**
      (Round2 #13). Shipped in 04160dc: OR'd finished_at into window.
- `[x]` **Inbox YAML injection via filename** (Round2 #14). Shipped
      in 04160dc: `_yaml_quote()` single-quotes filenames in
      frontmatter.
- `[x]` **`emit_event` out-of-transaction** (Round2 #16). Shipped in
      04160dc: optional `session=` kwarg; worker.py callsites now
      commit atomically with task-status. Poller callsites keep
      independent scope.
- `[-]` **`syncer` retention by tick count** (Round2 #18). Moot —
      syncer disabled per OG6.
- `[x]` **`search_vault` unbounded** (Round2 #15). Shipped in 04160dc:
      MCP tool clamps caller limit to [1, 50].
- `[x]` **Scheduler swallows per-job failures** (Codex #6). Already
      fixed in 63d51cd.
- `[x]` **Dispatcher `_worker_seq` mutation** (Codex #3). Shipped in
      04160dc: pool.submit takes optional `worker_id=`, main.py reuses
      the allocated ID.
- `[-]` **pyright + ruff red** (Codex #5). Skip — process work,
      touch-as-we-go.
- `[x]` **Theme investigations don't queue work** (Codex #2). Already
      addressed in bcd3e78 (fail-fast at MCP layer).
- `[-]` **`dive_custom` dropped** + memo `SPECIALTIES` excludes
      custom (Round2 #9/#10). Deliberate skip — re-enable once plan
      parser extracts specialty.
- `[-]` **Test coverage gaps: press_ca/press_us/inbox_watcher/syncer/
      search_vault/file_to_vault/ingest_source/requeue_dead_letter**
      (Round2 #20). Skip — process work.

**Sweep tally: 11 shipped (6 new in 04160dc + 5 already fixed),
5 deliberately deferred.**

---

## Section D — Monday 05:00 ET preflight

Smoke sequence to run before market open:

- `bash scripts/preflight.sh` → PASS
- `bash scripts/smoke.sh` → PASS (requires rate_limit=clear)
- `systemctl status 'praxis-*.service'` → all active
- `psql -c "SELECT status, COUNT(*) FROM tasks GROUP BY status"` →
  no pathological queue backup
- Last `surfaced_ideas` within 35min
- `rate_limit_state.status = clear`
- ntfy test push received on phone

---

## Section F — Open-ended research engine (full-stack build tonight)

Source spec: `scratch/open-ended-research-implementation-spec.md`.
This ships alongside the existing company-dive engine — does NOT
replace it. Ticker dives remain leaf execution; this layer sits above.

**Scope decision**: targeting Phases 1-6 (full MVP standard) tonight.
Phase 7 (refresh maintenance loop) deferred post-Monday.

### F.1 `[x]` Section F wired into the overnight agenda

### F.0 `[x]` Phase 0 — vault memory / research search layer
**Shipped** commit `e1e3723`. `praxis_core/vault/memory.py` with
two-stage keyword + Haiku rerank. Observer `search_vault` MCP tool
upgraded to use it. 13 unit tests.
**Load-bearing. Ships first. Every downstream phase calls it.**

Files:
- `praxis_core/vault/memory.py` — `search_vault_memory(query, limit,
  scope)`. Two-stage: cheap keyword-overlap filter → Haiku rerank
  with rationale. Returns ranked `VaultHit` list.
- `services/mcp/server.py` — upgrade existing `search_vault` tool
  to use the new ranked search (keep the name for observer muscle
  memory).

Design:
- **Stage 1 (keyword filter)**: walk target dirs per scope.
  Tokenize query + each doc (title + frontmatter tags + first
  2000 chars). Score by normalized term-overlap. Top 40 candidates.
- **Stage 2 (Haiku rerank)**: send candidates + query to Haiku
  with prompt "pick top N most relevant, emit {path, score 0-1,
  one-sentence rationale}". Cheap (~$0.03/call), understands
  semantic relevance.
- **Scope default**: search all vault areas unless caller restricts.
- **Cache**: 10-min LRU on (query, scope) so repeated calls from
  the same planner don't re-rerank.
- **Failure mode**: if Haiku unavailable (RL), return Stage 1
  results directly — still useful, just less smart.

Where it plugs in:
- `orchestrate_research` FIRST call — dedup against existing nodes.
- `gather_sources` per query — skip web if local coverage sufficient.
- `compile_research_node` — auto-discover `related:` links.
- `answer_question` — find sources that may already answer locally.
- `synthesize_crosscut_memo` — broader than one run's gathers.
- Observer `search_vault` MCP tool — real memory search, not grep.

Tests:
- `tests/unit/test_vault_memory.py` — stage 1 keyword filter,
  stage 2 rerank with stubbed LLM, scope filter, cache behavior,
  fallback when Haiku rate-limited.

### F.2 `[x]` Phase 1 — freeform entrypoint + planner
**Shipped** commit `73064ea`. `orchestrate_research` handler +
system prompt, `research_query` MCP tool, all 6 new TaskType enum
entries + payload classes + resource-key extensions.
Files:
- `praxis_core/schemas/task_types.py` — add `ORCHESTRATE_RESEARCH`,
  `GATHER_SOURCES`, `COMPILE_RESEARCH_NODE`, `ANSWER_QUESTION`,
  `SCREEN_CANDIDATE_COMPANIES`, `SYNTHESIZE_CROSSCUT_MEMO`.
- `praxis_core/schemas/payloads.py` — 6 payload classes per spec,
  add to `PAYLOAD_MODELS` map.
- `praxis_core/tasks/enqueue.py` — extend `_resource_key_for` with
  `theme:` / `basket:` / `question:` / `concept:` / `crosscutting:`
  families. (Bonus: closes A.3.1.)
- `handlers/orchestrate_research.py` — Sonnet planner. Takes freeform
  prompt, searches the vault for nearest-neighbor themes/questions/
  concepts FIRST (dedup before creation), emits structured JSON plan.
- `handlers/prompts/orchestrate_research.py` — system prompt.
- `handlers/__init__.py` — register handler.
- `services/mcp/server.py` — new `research_query(prompt,
  research_priority=5)` MCP tool.
- `praxis_core/tasks/validators.py` — light validator for the
  investigation plan file.

Design decisions (made tonight, not up for discussion mid-build):
- **Wiki dedup first**: `orchestrate_research` searches existing
  `themes/`, `questions/`, `concepts/` by slug similarity + keyword
  match BEFORE proposing new node creation. Prefer `action: update`
  over `action: create` where fuzzy matches exist.
- **Candidate caps**: planner may propose up to 15 candidate
  tickers, but MUST shortlist via Phase-5 screening before any hit
  `orchestrate_dive`. Prevents "launch 15 expensive dives per
  prompt" risk.
- **Budget**: `research_priority` maps to total USD ceiling
  (priority 5 → $15 whole research flow incl. dives; priority 9 →
  $50). Individual tasks inherit their slice.
- **Constitution/steering**: every new LLM call in this system
  reads `constitution_prompt_block` + `recent_steering` — flows
  naturally into broad-topic research.

### F.3 `[x]` Phase 2 — persistent retrieval
**Shipped** commit `73064ea`. `praxis_core/vault/sources.py` with
`persist_web_source`, `handlers/gather_sources.py` with Sonnet +
WebSearch/WebFetch/curl, `persist_source` MCP tool. 7 unit tests.
Files:
- `praxis_core/vault/sources.py` — `persist_web_source(url, title,
  body_text, site=None, publish_date=None)` — writes to
  `_raw/manual/<YYYY-MM-DD>/<slug>.md` with frontmatter. Dedup by
  URL hash.
- `handlers/gather_sources.py` — Sonnet + WebSearch + WebFetch + curl.
  Runs retrieval queries, fetches promising pages, persists,
  returns source paths.
- `handlers/prompts/gather_sources.py` — prompt.
- Validator: at least 1 source persisted, all source paths resolve.

### F.4 `[x]` Phase 3 — theme/question/concept compilation
**Shipped** commit `73064ea`. `handlers/compile_research_node.py`
pre-writes skeleton scaffolds per node_type, then Sonnet Edits
in place. Validator checks for `## Evidence` section.
Files:
- `handlers/compile_research_node.py` — non-company analogue of
  `compile_to_wiki`. Modes: `theme`, `question`, `concept`. Reads
  source paths, updates/creates the node file with frontmatter +
  evidence section + wikilinks to related nodes. Preserves prior
  body.
- `handlers/prompts/compile_research_node.py`
- Validator: file exists, frontmatter valid, evidence section
  present, ≥1 source wikilink.

### F.5 `[x]` Phase 4 — question answering
**Shipped** commit `73064ea`. `handlers/answer_question.py`, budget
scales with research_priority. Validator: status transitions + answer
section populated.
Files:
- `handlers/answer_question.py` — reads question body + linked
  sources + related themes, writes answer section, transitions
  `status` from `open` → `partial` | `answered` based on evidence
  coverage.
- `handlers/prompts/answer_question.py`
- Validator: status is updated, answer body OR partial-rationale
  exists, ≥1 citation.

### F.6 `[x]` Phase 5 — candidate screening
**Shipped** commit `73064ea`. `handlers/screen_candidate_companies.py`
ranks via Sonnet + fundamentals MCP; enqueues `orchestrate_dive`
for top-N deep_dive verdicts under a child investigation referencing
the parent via `entry_nodes`.
Files:
- `handlers/screen_candidate_companies.py` — ranks candidate
  tickers by exposure purity + investability + coverage-freshness.
  Emits per-ticker verdict: `deep_dive` | `note_only` | `reject`.
  Enqueues `orchestrate_dive` for `deep_dive` names via existing
  company flow (no changes to that flow).
- `handlers/prompts/screen_candidate_companies.py`
- Validator: verdicts present for every candidate, justification
  per verdict.

### F.7 `[x]` Phase 6 — cross-cutting synthesis
**Shipped** commit `73064ea`. `handlers/synthesize_crosscut_memo.py`
uses Opus, gates on sibling task completion with 4h wall-clock cap
(same pattern as A.1). Validator checks memo has all required
sections (Thesis / Evidence / Equity ranking / Known vs uncertain).
Files:
- `handlers/synthesize_crosscut_memo.py` — Opus. Reads the
  investigation's themes, questions, compiled answers, gathered
  sources, and any completed ticker memos. Writes
  `memos/<YYYY-MM-DD>-<memo_handle>.md` with required sections:
  thesis, transmission map, evidence, equity ranking, known vs
  uncertain, open questions, related links.
- `handlers/prompts/synthesize_crosscut_memo.py`
- Validator: memo exists, frontmatter valid, ≥1 theme link, ≥1
  question link, ≥1 source citation, ranking section (if present)
  matches body evidence.

### F.8 `[ ]` Phase 7 — refresh/maintenance loop (deferred)
Post-Monday. `refresh_research_node` + scheduler wiring.

### F.9 `[x]` Tests (phased with each handler)
Shipped: test_vault_memory (13), test_research_payloads (10),
test_orchestrate_research_parsing (7), test_persist_web_source (7),
test_research_validators (11), test_synthesize_memo_skeleton_cap (5).
Total +53 tests; full unit suite now 332 green.
Integration smoke deferred — needs Anthropic upstream clear.
- `tests/unit/test_research_payloads.py`
- `tests/unit/test_orchestrate_research_parsing.py`
- `tests/unit/test_persist_web_source.py`
- `tests/unit/test_compile_research_node.py`
- `tests/unit/test_answer_question.py`
- `tests/unit/test_screen_candidate_companies.py`
- `tests/unit/test_synthesize_crosscut_memo.py`
- Smoke: end-to-end `research_query("research Hormuz fertilizer
  beneficiaries")` — will not run live tonight due to Anthropic
  throttle, but enqueue + task-row assertions should work.

### F.10 `[x]` Commits
Landed as: e1e3723 (Phase 0), 73064ea (Phases 1-6 + validators +
tests + MCP tools), 0bc6593 (A.1 cap), 36f2c1f (A.2+A.3+A.5).

---

## Section E — In-session observations (not Codex)

- `[ ]` **Multiple probe tasks claim together during probing.**
      `_dispatch_tick` runs the claim loop over every available
      pool slot when status=probing. Cheap (probes are Haiku
      synthetic) + race-benign (first success flips state), but a
      bit wasteful. Gate at 1 probe in flight.
- `[ ]` **Anthropic Max weekly/tier cap overnight.** Observed 6+
      consecutive probe failures with extending cooldowns. Not our
      bug — operational. Consider conserving quota overnight:
      either reduce `dispatcher_pool_size` to 2 when RL hits ≥3
      consecutive misses, OR gate dive dispatch on a probe-success
      streak.
