# FOLLOWUPS — open items for discussion

This tracks audit items we explicitly deferred, want to discuss together, or still need
real-world validation. Grouped by theme.

---

## ✅ Resolved

- **#1 CLI invoker validation** — tested against real `claude -p` 2.1.114; fixed flag issues
  (`--max-turns` removed, replaced with `--max-budget-usd`), parser handles real event types
  including structured `rate_limit_event`. Two passing real-CLI tests gated by
  `PRAXIS_TEST_REAL_CLAUDE=1`.
- **#2 Pgcrypto** — verified: `CREATE EXTENSION IF NOT EXISTS "pgcrypto"` works for regular
  user (no superuser needed). All 18 formerly-skipped integration tests now pass against
  local Postgres.
- **#5 compile_to_wiki validator** — now requires ≥100 chars in notes.md, backlink to
  analysis_path, non-empty journal.md. Four new tests cover each failure mode.
- **#6 Orchestrator respects LLM plan** — new `handlers/_plan_parser.py` parses the
  investigation's `## Plan` section; orchestrator enqueues what the LLM wrote (order
  preserved, dedupe, unknown types skipped). Falls back to default sequence if unparseable.
- **#8 Dead-letter recovery** — `list_dead_letters`, `inspect_dead_letter`,
  `requeue_dead_letter` MCP tools; dashboard `Dead letter` section with prominent red alert
  when queue non-empty.
- **#13 boost_ticker duration_min** — dropped the unused param.
- **#14 Dashboard refresh cadence** — 10s interval, visible spinner, manual refresh button,
  stale indicator on error.

---

## Blocked by human-in-the-loop (need you)

### Tailscale ACLs for MCP + dashboard
Currently MCP is `127.0.0.1` (fine), dashboard binds `0.0.0.0:8080`. On Tailscale-only
network this is OK, but verify on Ryzen:
- Ryzen has no public IP exposing 8080, OR
- Tailscale ACLs restrict `praxis-server:8080` to your tailnet identity only.

Worth a 5-min manual check after bootstrap.

---

## Discussion items

### Handler prompts — tune against real filings (was audit #5)
Every handler has a plausible-but-untested system prompt against real filings. Expected
first-run failures:
- Triage returns non-JSON (code fences) → validator fails → partial status
- Compile skips citing → now caught by strengthened validator, but retries cost money
- Dive tasks produce thin sections → low-quality notes.md

**Plan when you're back on Ryzen:**
- Drop one real 8-K into the manual inbox; watch it flow through the pipeline.
- Inspect triage.md, analysis.md, signals.json, then the compiled notes.md.
- Tune prompts; re-run.
- 2-4 hours of hands-on work.

Known likely tweaks:
- Explicit "JSON only, no code fences" instruction to triage.
- `compile_to_wiki` needs to emit an explicit "files touched" list so the validator can
  double-check (it already enforces backlink + min length).
- Dive tasks should be shown an example note structure.

---

## Stretch / later

### Deploy strategy for handler prompts
Currently prompt changes = code commit + deploy. Option: move prompts to
`vault/_prompts/<task_type>.md` so changes take effect on next task without systemctl
restart. Trade-off: new dependency between vault and code.

### Observer session resume
Observer Claude chat sessions don't persist. If your Mac sleeps mid-conversation, you
start over. Options: `claude --resume`, or convention of filing transcripts to
`vault/_observer_sessions/<date>.md`.

### Claude Desktop integration
Manual-ingest path via Desktop. Deferred past Monday. When ready: custom Desktop skill
that reads selected text + calls `ingest_source` MCP tool.

### Market data + IBKR TWS
Out of Monday scope. Plan:
- Service `praxis-market-data` reading IBKR TWS (or yfinance fallback), writes real-time
  prices to `prices` table.
- New task type `check_price_threshold` triggered by scheduler.
- MCP tool `get_price(ticker)` for observer / dive handlers.

### Vault migration from autoresearch
Read `~/dev/praxis-autoresearch/vault/` + S3-backed memos, dedup/reformat into new
structure. Dry-run mode (emit diff, human review). LLM-assisted for semantic reframing.

### Concept/theme split vs merger
Kept them separate for now. Revisit once there's actual content and we see whether the
split is useful in practice.

### Multi-observer coordination
If two observer Claude sessions run at once, both can call `cancel_task()` etc. No
serialization — mostly fine (idempotent) but edge cases around `boost_ticker` could
double-boost. Low priority.

### X bookmarks poller
Deferred past Monday. Week 2: `~/praxis-inbox/x-bookmarks/` drop directory with X API
exports or browser-extension dumps.

### Cost-aware invoker fallback
When daily spend exceeds a budget, auto-downshift to cheaper models (Opus→Sonnet). Not
yet implemented — requires a spend-aggregation query and a per-task model-override layer.

### pgvector semantic search for observer queries
pgvector extension is installed but unused. Could index compiled notes for semantic vault
search via observer MCP. Not needed for Monday.

---

## NOT going to be fixed (by design)

- **No API fallback from CLI.** You explicitly want API mode to require flag + restart.
- **No in-memory queue optimization.** Postgres is the queue. Don't add Redis.
- **No multi-repo.** Monorepo stays.
- **No session resume as a persistence mechanism.** Vault is the memory.
