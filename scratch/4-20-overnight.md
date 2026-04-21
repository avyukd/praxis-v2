# 4-20 Overnight Plan

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

### A.1 `[ ]` `synthesize_memo` has no wall-clock cap on transient retries 🟠
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

### A.2 `[ ]` MCP `append_principle` shadows imported symbol (F811) 🟠
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

### A.3 `[ ]` `file_to_vault` no symlink-resolved path safety 🔒
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

### A.5 `[ ]` Drop unused `SYSTEM_PROMPT` import in surface_ideas 🟡
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

## Section C — Deferred post-Monday

Real but not blocking. Track here so they don't get lost.

- `[-]` **Resource-key contract missing for surface_ideas /
      cleanup_sessions / refresh_backlinks / ticker_index** (Round2 #2).
      All get `resource_key=None`. For surface_ideas specifically
      this lets multiple runs fan out in parallel. Nice-to-have.
- `[-]` **Dashboard `innerHTML` XSS** (Round2 #17). Only Avyuk uses
      it. Post-Monday security pass.
- `[-]` **`notify` uses sync `httpx.Client` in async handler**
      (Round2 #6). ntfy is fast enough today; swap to AsyncClient
      when we touch that file.
- `[-]` **`refresh_index` misses `index.md` stubs** (Round2 #11).
      Minor graph hygiene.
- `[-]` **`cleanup_sessions` skips Pydantic validation** (Round2 #12).
      Consistency cleanup.
- `[-]` **`generate_daily_journal` window on `started_at` only**
      (Round2 #13). Edge case for midnight-crossing tasks.
- `[-]` **Inbox YAML injection via filename** (Round2 #14). Avyuk-only
      surface.
- `[-]` **`emit_event` out-of-transaction** (Round2 #16). Real but
      high blast radius — touch all event call sites. Post-Monday.
- `[-]` **`syncer` retention by tick count** (Round2 #18). Syncer
      disabled per OG6; moot.
- `[-]` **`search_vault` unbounded** (Round2 #15). Small vault today.
- `[-]` **Scheduler swallows per-job failures** (Codex #6). Add
      consecutive-failure counter + alert. Post-Monday.
- `[-]` **Dispatcher `_worker_seq` mutation** (Codex #3). Structural
      tidy.
- `[-]` **pyright + ruff red** (Codex #5). Clean file-by-file as we
      touch them.
- `[-]` **Theme investigations don't queue work** (Codex #2). Feature
      gap; rarely used.
- `[-]` **`dive_custom` dropped** + memo `SPECIALTIES` excludes
      custom (Round2 #9/#10). Deliberate — re-enable once plan parser
      extracts specialty.
- `[-]` **Test coverage gaps: press_ca/press_us/inbox_watcher/syncer/
      search_vault/file_to_vault/ingest_source/requeue_dead_letter**
      (Round2 #20). Process work.

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

### F.0 `[ ]` Phase 0 — vault memory / research search layer
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

### F.2 `[ ]` Phase 1 — freeform entrypoint + planner
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

### F.3 `[ ]` Phase 2 — persistent retrieval
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

### F.4 `[ ]` Phase 3 — theme/question/concept compilation
Files:
- `handlers/compile_research_node.py` — non-company analogue of
  `compile_to_wiki`. Modes: `theme`, `question`, `concept`. Reads
  source paths, updates/creates the node file with frontmatter +
  evidence section + wikilinks to related nodes. Preserves prior
  body.
- `handlers/prompts/compile_research_node.py`
- Validator: file exists, frontmatter valid, evidence section
  present, ≥1 source wikilink.

### F.5 `[ ]` Phase 4 — question answering
Files:
- `handlers/answer_question.py` — reads question body + linked
  sources + related themes, writes answer section, transitions
  `status` from `open` → `partial` | `answered` based on evidence
  coverage.
- `handlers/prompts/answer_question.py`
- Validator: status is updated, answer body OR partial-rationale
  exists, ≥1 citation.

### F.6 `[ ]` Phase 5 — candidate screening
Files:
- `handlers/screen_candidate_companies.py` — ranks candidate
  tickers by exposure purity + investability + coverage-freshness.
  Emits per-ticker verdict: `deep_dive` | `note_only` | `reject`.
  Enqueues `orchestrate_dive` for `deep_dive` names via existing
  company flow (no changes to that flow).
- `handlers/prompts/screen_candidate_companies.py`
- Validator: verdicts present for every candidate, justification
  per verdict.

### F.7 `[ ]` Phase 6 — cross-cutting synthesis
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

### F.9 `[ ]` Tests (phased with each handler)
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

### F.10 `[ ]` Commit
Single commit per phase to keep the diff reviewable.

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
