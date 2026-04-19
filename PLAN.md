# praxis-v2 build plan

Written 2026-04-18. Target ship: Monday 2026-04-20.

This is a reference doc, not a narrative. Keep it current as decisions change.

---

## 1. Tenets

1. **Signal surfacing** — every 8-K, press release, 10-Q/K in chosen market-cap bands gets triaged and analyzed, reactively and reliably, with priority during 8-10am ET.
2. **Deep research** — 5-7 Opus-driven deep dives per day, compounding over time into a cross-linked research wiki. Depth emerges from compounding narrow passes, not single long tasks.
3. **Reliability** — system runs unattended on a remote headless host. Any process can be killed at any instant without corrupting shared state. Silent failure is the enemy: dead-man's switch pushes to phone within 5 min of stalls.

Load-bearing invariant: **any process can crash at any instant without corrupting shared state.** Every design choice below flows from this.

---

## 2. Mental model — PM / analyst org

- **User = PM.** Sets strategy, reprioritizes, cancels, redirects.
- **Observer Claude = chief of staff.** Translates PM intent into DB state. Surfaces findings.
- **Dispatcher = research director.** Assigns work respecting priorities and rate limits.
- **Workers = analysts.** Specialized by task type (via system prompts), execute one task each.
- **Default**: analysts work on what's interesting per heuristics. PM intervention is optional.

---

## 3. Three loops over a shared wiki

**Loop A — reactive ingest-to-knowledge.** External event → raw → triage (Haiku) → analyze (Sonnet) → compile-to-wiki (Sonnet) → optional notify. High cadence.

**Loop B — directed deep dive.** Investigation opened (by observer or heuristic) → orchestrator (Sonnet) plans specialist dives → specialists (Opus) execute → synthesize_memo (Opus). 5-7 Opus dives/day.

**Loop C — graph walks and agent-initiated ideation.** *(Deferred to week 2.)* Scheduled walks over wiki graph → file new questions or promote to investigations.

All three loops use the same dispatcher, same worker pool, same rate limit, same vault with resource locks. Differ only in trigger and priority.

---

## 4. Tech stack

| Component | Choice |
|---|---|
| Language | Python 3.13 via `uv` |
| DB | Postgres 16 (+ pgvector for future, unused Monday) |
| ORM | SQLAlchemy 2.x async |
| Models | Pydantic v2 |
| Schema migrations | Alembic |
| Process supervisor | systemd on Ryzen, honcho/overmind on Air |
| LLM invocation | `claude -p` CLI (Max subscription); API available per-process via flag+restart |
| MCP | official Python SDK |
| Backup | `restic` → S3 hourly |
| Sync | Syncthing (Ryzen ↔ Air); S3 rclone-mount for work laptop |
| Networking | Tailscale across all machines |
| Alerts | ntfy.sh |
| Dashboard | Single-file FastAPI served by Caddy |

**CLI trick discipline**: always `env.pop("ANTHROPIC_API_KEY"); env.pop("CLAUDE_API_KEY")` before spawning. `stdin=/dev/null`. `--output-format=stream-json --verbose`. Unique `--session-id` and isolated cwd per invocation. Hard wall-clock timeout with SIGKILL.

---

## 5. Monorepo layout

```
praxis-v2/
  PLAN.md                        # this file
  CLAUDE.md                      # project-level conventions (separate from vault/CLAUDE.md)
  pyproject.toml                 # uv workspace
  alembic/                       # DB migrations
  praxis_core/                   # shared library
    __init__.py
    db/
      models.py                  # SQLAlchemy models
      session.py
    schemas/                     # Pydantic schemas for task payloads, artifacts
    llm/
      invoker.py                 # LLMInvoker protocol, CLIInvoker, APIInvoker
      stream_parser.py
      rate_limit.py              # state machine, probe logic
    vault/
      writer.py                  # atomic tempfile+rename helpers
      reader.py
      conventions.py             # path builders, frontmatter helpers
    tasks/
      lifecycle.py               # claim/heartbeat/release/validate helpers
      validators.py              # per-task-type artifact validators
    observability/
      heartbeat.py
      events.py
      cost.py
  handlers/                      # one file per task type
    triage_filing.py
    analyze_filing.py
    compile_to_wiki.py
    notify.py
    orchestrate_dive.py
    dive_business.py
    dive_moat.py
    dive_financials.py
    synthesize_memo.py
    refresh_index.py
    lint_vault.py
    generate_daily_journal.py
  services/
    pollers/
      edgar_8k.py                # EDGAR 8-K poller, writes _raw + enqueues triage
      inbox_watcher.py           # watches ~/praxis-inbox/ for human drop-in
    dispatcher/
      main.py                    # main loop: claim, assign, heartbeat
      worker.py                  # worker subprocess; runs one task
      pool.py                    # pool management
    scheduler/
      main.py                    # internal cadence tasks (daily lint, index refresh, etc.)
    mcp/
      server.py                  # MCP tools for observer Claude
    dashboard/
      app.py                     # single-file FastAPI
      static/index.html
    syncer/
      main.py                    # restic to S3 wrapper
  infra/
    systemd/                     # .service units for Ryzen
    Procfile                     # for local dev on Air via overmind
    caddy/Caddyfile
    ntfy/topics.yaml
    deploy.sh                    # git pull + uv sync + systemctl restart
    bootstrap.sh                 # one-time Ryzen setup (packages, users, dirs)
    .env.example
  tests/
    integration/
    unit/
```

---

## 6. Postgres schema (final)

```sql
-- Task state
CREATE TABLE tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  type TEXT NOT NULL,                     -- 'triage_filing', 'dive_moat', etc.
  priority INT NOT NULL,                  -- 0 (highest) to 4
  status TEXT NOT NULL,                   -- queued | running | partial | success | failed | dead_letter | canceled
  model TEXT NOT NULL,                    -- 'haiku' | 'sonnet' | 'opus' | 'none'
  payload JSONB NOT NULL,                 -- Pydantic-validated per task type
  dedup_key TEXT UNIQUE,                  -- for ON CONFLICT DO NOTHING on enqueue
  resource_key TEXT,                      -- e.g. 'company:NVDA' for mutex
  investigation_id UUID REFERENCES investigations(id),
  parent_task_id UUID REFERENCES tasks(id),
  depends_on UUID[],                      -- gating task ids

  lease_holder TEXT,                      -- worker id
  lease_expires_at TIMESTAMPTZ,
  attempts INT NOT NULL DEFAULT 0,
  rate_limit_bounces INT NOT NULL DEFAULT 0,
  max_attempts INT NOT NULL DEFAULT 3,

  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,

  last_error TEXT,
  validation_result JSONB,                -- {ok: [...], missing: [...], malformed: [...]}
  telemetry JSONB                         -- {tokens_in, tokens_out, cost_usd, duration_s}
);

CREATE INDEX idx_tasks_dispatch ON tasks (status, priority, created_at)
  WHERE status IN ('queued', 'partial');
CREATE INDEX idx_tasks_resource ON tasks (resource_key, status)
  WHERE status = 'running';

-- Investigations (the "PM assignment" unit)
CREATE TABLE investigations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  handle TEXT UNIQUE NOT NULL,            -- e.g. 'nvda-ai-capex-digestion'
  status TEXT NOT NULL,                   -- active | paused | resolved | abandoned
  scope TEXT NOT NULL,                    -- company | theme | concept | basket | cross-cutting
  initiated_by TEXT NOT NULL,             -- user | heuristic | question_promotion | surface_task
  hypothesis TEXT,
  entry_nodes TEXT[],                     -- wiki node paths
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ,
  last_progress_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  artifacts TEXT[]                        -- paths in vault
);

-- Rate limit singleton
CREATE TABLE rate_limit_state (
  id INT PRIMARY KEY DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'clear',   -- clear | limited | probing
  limited_until_ts TIMESTAMPTZ,
  consecutive_hits INT NOT NULL DEFAULT 0,
  last_hit_ts TIMESTAMPTZ,
  probe_task_id UUID,
  CHECK (id = 1)
);
INSERT INTO rate_limit_state (id, status) VALUES (1, 'clear');

-- Heartbeats
CREATE TABLE heartbeats (
  component TEXT PRIMARY KEY,
  last_heartbeat TIMESTAMPTZ NOT NULL DEFAULT now(),
  status JSONB
);

-- Events (structured append-only log)
CREATE TABLE events (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  component TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload JSONB
);
CREATE INDEX idx_events_recent ON events (ts DESC);

-- Sources (replaces source_index.json)
CREATE TABLE sources (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dedup_key TEXT UNIQUE NOT NULL,         -- accession# or URL hash
  source_type TEXT NOT NULL,              -- 'filing_8k' | 'filing_10q' | 'press' | 'x_bookmark' | 'manual'
  vault_path TEXT NOT NULL,
  ticker TEXT,
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  metadata JSONB
);

-- System state (rate limits, feature flags, etc.)
CREATE TABLE system_state (
  key TEXT PRIMARY KEY,
  value JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Dead letter
CREATE TABLE dead_letter_tasks (
  id UUID PRIMARY KEY,
  original_task JSONB NOT NULL,
  final_error TEXT,
  failed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Signals fired (notification history)
CREATE TABLE signals_fired (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id UUID REFERENCES tasks(id),
  ticker TEXT,
  signal_type TEXT NOT NULL,
  urgency TEXT NOT NULL,
  payload JSONB NOT NULL,
  fired_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 7. Vault layout (AI-managed, human read-only)

Lives at `~/vault/` on Ryzen. Synced to Air via Syncthing. Backed up via restic to S3 hourly.

```
vault/
  CLAUDE.md                      # schema + conventions for agents
  INDEX.md                       # auto-maintained MOC (Karpathy pattern)
  LOG.md                         # auto-maintained append-only record

  _raw/                          # firehose input, immutable
    filings/{8k,10q,10k}/<accession>/filing.txt + meta.json
    press/YYYY-MM-DD/<slug>.md
    x_bookmarks/YYYY-MM-DD/<id>.md     # wiring deferred
    desktop_clips/YYYY-MM-DD/<slug>.md # deferred
    manual/YYYY-MM-DD/<slug>.md        # drop-in via inbox_watcher

  _analyzed/                     # per-event first-pass analysis
    filings/{8k,10q,10k}/<accession>/{triage,analysis,signals}.{md,json}
    manual/<id>/triage.md

  companies/<TICKER>/
    notes.md                     # living compiled knowledge
    thesis.md                    # optional, evolving
    memos/YYYY-MM-DD-<handle>.md # dated single-company memos
    journal.md                   # append-only per-ticker log
    data/                        # structured extracts

  people/<slug>.md
  themes/<slug>.md               # time-bound narratives
  concepts/<slug>.md             # evergreen frameworks + specialty knowledge
  questions/<slug>.md            # open inquiries
  investigations/<handle>.md     # multi-task research threads (PM assignments)
  memos/YYYY-MM-DD-<handle>.md   # cross-cutting memos (cohort, theme syntheses)
  journal/YYYY-MM-DD.md          # machine-generated daily summary
```

**Writing rules:**
- All vault writes via `praxis_core.vault.writer` which does tempfile + rename atomically.
- Bidirectional `[[wikilinks]]` with `## Related` sections on every note.
- YAML frontmatter on every note: `type, status, data_vintage, tags, links`.
- Every quantitative claim cites a source in `_raw/` or fundamentals MCP.
- Compile tasks target 5+ affected pages (Karpathy: 10-15).
- Updates to `INDEX.md` on every compile; daily rebuild task re-syncs.

**Resource locks (coarse-grain for Monday):**
- `resource_key = "company:<TICKER>"` — one write at a time per company
- `resource_key = "theme:<handle>"` — one write at a time per theme
- `resource_key = "concept:<handle>"` — one write at a time per concept
- Dispatcher enforces: skip dispatching any task whose `resource_key` is held by a currently-running task.

---

## 8. Task taxonomy — Monday scope (11 types)

| Task type | Model | Artifacts (validator checks) | Typical time |
|---|---|---|---|
| `triage_filing` | haiku | `_analyzed/.../triage.md`, `_analyzed/.../triage.json` (score ∈ 1-5, category, one_sentence_why); if score ≥ 3: enqueued `analyze_filing` | 5s |
| `analyze_filing` | sonnet | `_analyzed/.../analysis.md`, `_analyzed/.../signals.json` (Pydantic-validated); if trade_relevant: enqueued `notify` and `compile_to_wiki` | 60s |
| `compile_to_wiki` | sonnet | Updated `companies/<TICKER>/notes.md` + updated `companies/<TICKER>/journal.md` + updated `INDEX.md` + appended `LOG.md`; must touch ≥3 files | 30s |
| `notify` | none | row in `signals_fired` + ntfy push + `_analyzed/.../notify.log` | <1s |
| `orchestrate_dive` | sonnet | Updated `investigations/<handle>.md` with plan; enqueued specialist tasks | 10s |
| `dive_business` | opus | Updated `companies/<TICKER>/notes.md § Business`; appended investigation log | 2min |
| `dive_moat` | opus | Updated `companies/<TICKER>/notes.md § Moat`; appended investigation log | 2min |
| `dive_financials` | opus | Updated `companies/<TICKER>/notes.md § Financials` + `companies/<TICKER>/data/*.json` | 2min |
| `synthesize_memo` | opus | `companies/<TICKER>/memos/YYYY-MM-DD-<handle>.md`; updated investigation status=resolved | 3min |
| `refresh_index` | haiku | Rewritten `INDEX.md` | 30s |
| `lint_vault` | sonnet | `journal/YYYY-MM-DD-lint.md` report | 60s |
| `generate_daily_journal` | haiku | `journal/YYYY-MM-DD.md` | 15s |

Validators live in `praxis_core/tasks/validators.py`, one per task type. Validator signature: `(task, vault_root) -> ValidationResult(ok: list[Path], missing: list[Path], malformed: list[tuple[Path, str]])`.

Partial-success policy: if `missing` or `malformed` non-empty → task → `partial` status, remediation task enqueued with narrower scope and lower `max_attempts`.

---

## 9. Priority policy

Five lanes, weighted fair dispatch, age-weighted bumps.

| Tier | Lane | Min % of slots | Examples |
|---|---|---|---|
| P0 | Intraday filings 8-10am ET | 40% | `triage_filing`, `analyze_filing`, `notify` on 8-K |
| P1 | Filings off-hours + observer requests | 25% | Off-hours triage/analyze; observer `open_investigation` |
| P2 | Loop B dives (Opus) | 15% | `dive_*`, `synthesize_memo` |
| P3 | Human-dropped sources | 10% | Manual inbox items |
| P4 | Maintenance + graph walks | 10% | `lint_vault`, `refresh_index`, `generate_daily_journal` |

Age-weighted bump: any task older than 30 min gets +1 tier. Market-hours detection: weekdays 8am-4pm ET excluding US market holidays (hardcoded list for Monday, proper calendar later).

---

## 10. LLMInvoker contract

```python
class LLMInvoker(Protocol):
    def run(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: Literal["haiku", "sonnet", "opus"],
        max_turns: int,
        timeout_s: int,
        mcp_config: dict | None,
        allowed_tools: list[str],
        session_dir: Path,          # isolated cwd
    ) -> LLMResult: ...

class LLMResult(BaseModel):
    text: str
    tool_calls: list[ToolCall]
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None
    duration_s: float
    finish_reason: Literal["stop", "max_turns", "rate_limit", "timeout", "error"]
    raw_events: list[dict]          # for debugging
```

`CLIInvoker` implementation:
- Spawns `claude -p --output-format=stream-json --verbose --model=<map> --session-id=<uuid> --max-turns=<n>`
- `env.pop("ANTHROPIC_API_KEY", None); env.pop("CLAUDE_API_KEY", None)` — non-negotiable
- `stdin = DEVNULL`
- `cwd = session_dir` (unique per invocation)
- Stream parser reads events line by line; tracks last-event timestamp; kills subprocess if no event for 60s
- Hard wall-clock timeout → SIGTERM, 10s grace, SIGKILL
- Returns `finish_reason="rate_limit"` on detected rate limit event

`APIInvoker`: uses Anthropic SDK. Only instantiated when `PRAXIS_INVOKER=api` is set in process env. Restart required to switch. Never a fallback.

Model-to-CLI-flag mapping:
- `haiku` → `claude-haiku-4-5-20251001`
- `sonnet` → `claude-sonnet-4-6`
- `opus` → `claude-opus-4-7` (NOT the 1M context variant)

---

## 11. Rate limit state machine

Singleton row in `rate_limit_state`. Global across models (Max 20x, simplified).

States and transitions:

```
clear ──(any worker hits rate limit)──► limited
  limited_until = now() + backoff(consecutive_hits+1)
  consecutive_hits += 1

limited ──(now >= limited_until_ts)──► probing
  dispatch synthetic Haiku ping task

probing ──(probe succeeds)──► clear
  consecutive_hits = 0

probing ──(probe hits limit)──► limited
  consecutive_hits += 1, new backoff
```

Backoff: 180-300s (randomized) for hit #1, then 900s, 1800s, 3600s capped.

Guard for concurrent writers: `WHERE status != 'limited' OR last_hit_ts < now() - interval '30 seconds'`.

Dispatcher behavior:
- `status='clear'` → dispatch normally up to pool size (4)
- `status='limited' AND now() < limited_until_ts` → no dispatch
- `status='limited' AND now() >= limited_until_ts` → CAS to `probing`, dispatch one synthetic ping
- `status='probing'` → no dispatch

Observer tool: `clear_rate_limit()` for manual overrides.

Notifications: push on entering `limited` (once), on exiting to `clear` after >30min outage.

---

## 12. Observability

**Heartbeats:** every service writes `INSERT ... ON CONFLICT UPDATE` on `heartbeats` every 30s.

**Events:** append to `events` on meaningful state transitions (filing ingested, task dispatched, worker crashed, rate limit hit, probe result).

**Dashboard** (`services/dashboard/`): single-page HTML served by Caddy + FastAPI JSON endpoints. Views:
- System health: heartbeats, stale components flagged red
- Task queue: depth by type × priority, oldest queued age
- Rate limit status
- Today's cost rollup + token usage
- Last 50 events
- Investigation list + status

Load from phone over Tailscale.

**Dead-man's switch** (part of scheduler): every minute, check conditions, push via ntfy:
- Heartbeat stale > 5min for any component
- No `analyze_filing` success in 2hr during market hours (while EDGAR poller heartbeating)
- Dispatcher unreachable (no heartbeat in 2min)
- Rate-limit stuck > 30min during market hours
- Dead-letter count increased in last hour

---

## 13. Observer Claude MCP tools

```python
# Read
read_company_notes(ticker), read_thesis(ticker), read_investigation(handle)
search_vault(query, limit=10)
list_recent_analyses(hours=24), list_fired_signals(hours=24)
list_tasks(status=None, type=None, limit=50)

# Write (mutates Postgres; dispatcher respects next iter)
cancel_task(task_id)
reprioritize(task_id, new_priority)
boost_ticker(ticker, duration_min)      # bumps all tasks for ticker by 1 tier
open_investigation(ticker=None, theme=None, hypothesis=None)
pause_investigation(handle), resume_investigation(handle)
enqueue_dive(ticker, thesis_handle=None)

# Ops
rate_limit_status(), clear_rate_limit()
pool_status(), pause_pool(), resume_pool()

# Vault write (file to vault)
file_to_vault(path, content, linked_nodes=None)  # for "save this chat as a note"
ingest_source(content, title, source_hint=None)  # paste-and-ingest
```

---

## 14. Infra / deploy

**One-time Ryzen setup** (`infra/bootstrap.sh`):
- Ubuntu 24.04, install packages: postgresql-16, python3.13, uv, restic, caddy, syncthing
- Install Tailscale, join tailnet
- Create `praxis` user, dirs: `/opt/praxis-v2` (code), `~/vault`, `~/praxis-inbox`, `/var/lib/praxis`
- Initialize Postgres: create role + DB, run `alembic upgrade head`
- Install systemd units from `infra/systemd/`
- Configure restic repo pointed at S3
- Configure Syncthing, link with Air device ID
- Configure Caddy reverse proxy for dashboard + MCP

**Systemd units** (one per service):
```
praxis-dispatcher.service
praxis-scheduler.service
praxis-mcp.service
praxis-dashboard.service
praxis-syncer.service
praxis-poller-edgar-8k.service
praxis-poller-inbox.service
```
All with `Restart=always`, `RestartSec=10`, `StartLimitBurst=5`, `WatchdogSec=60`, `StandardOutput=journal`.

Workers are NOT separate services. Dispatcher spawns/manages workers as child processes.

**Local dev on Air** (`infra/Procfile` for overmind):
```
dispatcher: uv run python -m services.dispatcher.main
scheduler:  uv run python -m services.scheduler.main
mcp:        uv run python -m services.mcp.server
dashboard:  uv run python -m services.dashboard.app
edgar:      uv run python -m services.pollers.edgar_8k
inbox:      uv run python -m services.pollers.inbox_watcher
```

**Deploy script** (`infra/deploy.sh`):
```bash
cd /opt/praxis-v2
git pull
uv sync
alembic upgrade head
sudo systemctl restart 'praxis-*.service'
sudo systemctl status 'praxis-*.service' --no-pager
```

---

## 15. Build order

### Saturday (infra skeleton end-to-end)

**Morning (4h):**
- Monorepo scaffolded: `pyproject.toml` with uv workspace, directory tree
- `praxis_core/db/models.py` — all SQLAlchemy models per §6
- Alembic initialized, initial migration generated
- `praxis_core/vault/writer.py` — atomic tempfile+rename helper, tested
- `praxis_core/llm/invoker.py` — CLIInvoker skeleton, stream parser, rate-limit event detection
- Local dev environment on Air working: Postgres running locally, Procfile starts dispatcher+scheduler+mcp with no-op loops

**Afternoon (4h):**
- Dispatcher main loop: claim, lease, assign, heartbeat. No actual tasks yet.
- Rate-limit state machine + probe pattern
- One dummy task type end-to-end: `hello_world` task — prove claim→dispatch→worker→validate→mark-done cycle works
- Basic ntfy wiring + dead-man's switch loop
- Dashboard skeleton showing tasks table + heartbeats

**Evening (2h):**
- First real task type: `triage_filing` handler with a mock filing input
- `CLIInvoker.run()` actually invoking `claude -p` successfully with session isolation
- End of day: can enqueue a fake task, worker runs it through Claude CLI, writes validated artifact, marks done.

### Sunday (real task types + ingest pipelines)

**Morning (4h):**
- EDGAR 8-K poller: polls the filings RSS, writes `_raw/filings/8k/<accession>/filing.txt`, enqueues `triage_filing` with `dedup_key`
- Real `triage_filing` handler + validator
- Real `analyze_filing` handler + `signals.json` Pydantic schema + validator
- `notify` handler wired to ntfy
- Test end-to-end: ingest a recent real 8-K, see it flow through triage → analyze → notify

**Afternoon (4h):**
- `compile_to_wiki` handler — touches companies/, journal.md, INDEX, LOG
- Vault initial seed: `CLAUDE.md` schema doc, empty `INDEX.md`, `LOG.md`
- `inbox_watcher` poller: watches `~/praxis-inbox/`, ingests files as `_raw/manual/`
- Resource locks enforced in dispatcher
- Priority policy enforced (weighted fair + age bump)

**Evening (2h):**
- `orchestrate_dive` handler
- `dive_business`, `dive_moat`, `dive_financials` handlers (Opus, specialized system prompts)
- `synthesize_memo` handler
- Observer MCP tools: at minimum `list_tasks`, `cancel_task`, `open_investigation`, `rate_limit_status`
- Run a full investigation end-to-end on a test ticker on Air

### Monday morning (Ryzen cutover)

Ryzen box arrives. Target: running live by 8am ET filing window.

**Pre-market (before 8am ET):**
- Run `infra/bootstrap.sh` on Ryzen
- Git clone, `uv sync`, `alembic upgrade head`
- Configure Tailscale, Syncthing, restic, ntfy
- Copy `CLAUDE.md` seed + empty vault structure
- Enable systemd units
- Validate: all heartbeats green, dashboard loads from phone, ntfy test fires

**During market hours:**
- Monitor first real 8-Ks flowing through
- Have API-mode fallback one-liner ready: `sudo systemctl stop praxis-dispatcher && sudo systemctl edit praxis-dispatcher (set PRAXIS_INVOKER=api) && sudo systemctl start praxis-dispatcher`
- Iterate on validator failures / prompt tuning as filings roll in
- File questions/ for things observed

---

## 16. Explicitly deferred

**Week 2:**
- Loop C graph-walk tasks (`surface_theme_intersection`, `surface_concept_promotion`, etc.)
- 10-Q / 10-K handlers (chunking required, different prompt patterns)
- PR wire pollers
- Desktop clip ritual + MCP tool
- X bookmarks poller wiring
- Vault migration from autoresearch + S3 memos (LLM-assisted script)
- More specialist dive types (`dive_management`, `dive_competition`, `dive_valuation`, `dive_variant_perception`, `dive_kill_criteria`, `apply_concepts`, `apply_themes`)

**Week 3+:**
- Market data / IBKR TWS integration (start with yfinance for quotes only)
- Price/volume signal surfacing
- Cost-aware invoker (auto-downshift model on budget pressure)
- pgvector-backed semantic search for observer queries

**Never unless justified:**
- Multi-repo split (monorepo stays)
- API as a CLI fallback (flag-only, never automatic)
- Session resume as a persistence mechanism (vault is the memory)

---

## 17. Open questions still TBD

1. **Exact heuristics for auto-opening investigations.** Monday ships with only observer-triggered investigations. Week 2 add the heuristics.
2. **Basket / cohort artifact shape.** The autoresearch vault had these as top-level memos. Fits current design but schema TBD.
3. **Concept promotion threshold.** When does a recurring pattern become a first-class `concepts/<slug>.md`? Likely a Loop C task with its own rules.
4. **Memo naming convention details.** `companies/<TICKER>/memos/YYYY-MM-DD-<handle>.md` vs top-level `memos/YYYY-MM-DD-<handle>.md` — rule is single-ticker goes in company folder, cross-cutting goes top-level. Edge case: basket memo. Default to top-level.
5. **Investigation staleness threshold.** 7 days no log entry → stale. Might tune.

---

## 18. Tenet check — what protects each tenet

| Tenet | Protected by |
|---|---|
| Signal | EDGAR poller + `ON CONFLICT DO NOTHING` dedup + priority P0 during market hours + dead-man's switch on 2hr no-analyze |
| Depth | Loop B with Opus specialists + investigation state carrying context across tasks + compile touches 5+ pages rule |
| Reliability | Atomic vault writes + Postgres task state + lease-based claiming + systemd restart + structured exception boundaries + rate-limit probing + ntfy alerts |

If a change to the system breaks any of these, it doesn't ship.
