# FOLLOWUPS — open items for discussion

This tracks audit items we explicitly deferred, want to discuss together, or still need
real-world validation before trusting. Grouped by theme.

---

## Blocked by human-in-the-loop (need you)

### 1. CLI invoker — validate against real `claude -p`
**Status:** all tests pass against a fake `claude` binary, but we haven't confirmed:
- Does `--append-system-prompt` actually exist? Or is it `--system-prompt`?
- Does `--max-turns=N` work as expected?
- Does `--dangerously-skip-permissions` work when tool-using?
- What does a real `result` event's JSON look like? Real `assistant` event? Real rate-limit event?
- Does `--mcp-config <path>` work in headless mode?

**How to validate:** On Ryzen (or locally), once `claude` CLI is installed + logged in:
```bash
cd /tmp && claude -p "reply ok" --output-format=stream-json --verbose \
  --model=claude-haiku-4-5-20251001 --max-turns=1 --dangerously-skip-permissions
```
Observe every line it emits. Compare against `StreamParser._handle_event`. If the real output
uses different field names (e.g. `message.content[].text` vs `message.text`), adjust parser.
Then bump up to `--model=claude-sonnet-4-6` with a trivial file-write request and see if the
write succeeds.

**Risk if not validated:** Every handler will return `finish_reason="error"` or
`timeout`. The pipeline will dead-letter everything within hours of Monday launch.

---

### 2. Pgcrypto extension on Ryzen Postgres
Alembic migration `0001_initial.py` runs `CREATE EXTENSION IF NOT EXISTS "pgcrypto"`.
On Ubuntu 24.04 default Postgres install, this should work, but some managed Postgres
hosts (RDS, hosted) require superuser. Verify by running `alembic upgrade head` as
`praxis` user on Ryzen; if it fails, run the extension creation once as postgres superuser.

---

### 3. Tailscale ACLs for MCP + dashboard
Currently MCP is `127.0.0.1` (fine), dashboard binds `0.0.0.0:8080`. On Tailscale-only
network this is OK, but:
- Verify the Ryzen box has no public IP exposing 8080, OR
- Use Tailscale ACLs to restrict `praxis-server:8080` to your tailnet identity only.

Worth a 5-min manual check after bootstrap.

---

## Discussion items (punted from audit)

### 4. Handler prompts — tune against real filings
**Audit #5 from earlier.** Every handler has a plausible-but-untested system prompt.
Expected first-run failures:
- Triage returns non-JSON → validator fails → partial status
- Compile touches fewer than 5 files → validator fails → partial status
- Dive tasks skip citing sources → low-quality notes.md

**Plan:**
- Run one real 8-K through the pipeline manually (single-worker mode).
- Inspect each stage's output.
- Tune prompts iteratively.
- This is 2–4 hours of hands-on work on Ryzen. Do it before relying on output.

Key prompt tweaks we'll likely need:
- Add explicit JSON-only instruction to `triage_filing` (no code fences, no prose).
- Make `compile_to_wiki` emit an explicit "files touched" list so validator can verify.
- Show dives an example note structure so they follow conventions reliably.

### 5. compile_to_wiki validator is too lenient (audit #13)
Current check: "at least 3 files touched, including INDEX + LOG". A task that only appends
"x" to INDEX + LOG + an empty company notes.md passes — which is useless.

**Options:**
- Require a minimum content size on each touched file (e.g. notes.md must have > 500 chars after compile).
- Check mtime is after task start (proves this run actually wrote something, not stale file).
- Require a specific section header marker ("## <date>: <event>") in the company note.
- Verify backlinks exist (the wikilink to the analysis path is present in the company note).

My lean: all four. Cheap + deterministic.

### 6. Orchestrator ignores LLM's plan (audit #14)
`orchestrate_dive` hardcodes `dive_business → dive_moat → dive_financials → synthesize_memo`
regardless of what the LLM wrote in the investigation file. Defeats the "orchestrator as
planner" concept.

**Fix plan (not yet done):**
- Parse the investigation file's `## Plan` section.
- Extract each `dive_*` name mentioned.
- Enqueue only those, in the LLM's listed order.
- Fall back to the hardcoded sequence only if parsing fails.

---

## Stretch / later

### 7. Deploy strategy for changes to handler prompts
Right now, prompt changes = code commit + deploy. If you want to iterate on prompts
without a full deploy, consider moving prompts to vault (`vault/_prompts/<task_type>.md`)
so changes take effect on next task without systemctl restart. Trade-off: introduces a
new dependency between vault and code.

### 8. Dead-letter recovery UI
Currently dead-lettered tasks are in `dead_letter_tasks`. No UI to inspect them, no
mechanism to re-enqueue a batch after you fix the underlying issue. Add:
- Dashboard view listing dead-letter entries with their original payloads.
- MCP tool `requeue_dead_letter(task_id)` to put one back.
- MCP tool `inspect_dead_letter(task_id)` returning the full failure context.

### 9. Observer session resume
Observer Claude sessions don't persist. If you start a deep chat with observer and the
Mac sleeps, you start over. Options:
- Claude Code's `claude --resume <session>` (worth testing, reliability questionable).
- File conversation transcripts to `vault/_observer_sessions/<date>.md` as a convention.

### 10. Claude Desktop integration
Originally planned as a manual-ingest path. Deferred in Monday scope. When ready:
- Custom Desktop skill that reads user-selected text + calls `ingest_source` MCP tool.
- Verify multiple Desktop conversations don't fight over the same MCP transport.

### 11. Market data + IBKR TWS
Out of Monday scope. When ready:
- Service `praxis-market-data` that reads IBKR TWS socket (or yfinance fallback) and
  writes real-time prices to a `prices` table in Postgres.
- New task type `check_price_threshold` triggered by scheduler.
- MCP tool `get_price(ticker)` for observer / dive handlers.

### 12. Vault migration from autoresearch
Out of Monday scope. When ready:
- Read `~/dev/praxis-autoresearch/vault/` + any S3-backed memos.
- Dedup and reformat into new structure (`companies/<TICKER>/notes.md` etc.).
- Dry-run mode: emit a diff of what would change; human review before commit.
- LLM-assisted: use Claude to re-frame prose where semantic conventions changed.

### 13. Boost ticker duration (audit #25)
`boost_ticker(ticker, duration_min=60)` accepts `duration_min` but ignores it — the
priority bump is permanent for the task's lifetime. For now fine. When it matters:
- Add a scheduled "unboost" task that fires at `duration_min` later.
- OR store a `boosted_until` timestamp on the task and have dispatcher respect it when
  computing effective priority.

### 14. Dashboard refresh cadence (audit #26)
Hardcoded 5s auto-refresh. On mobile over Tailscale this is aggressive. Change to 10s,
add a visible spinner / stale-request indicator, allow manual refresh button.

### 15. Concept/theme split vs merger
Earlier we discussed whether themes and concepts should be separate directories or
merged under `nodes/` with a type frontmatter. Kept them separate for now. Revisit once
we have actual content and see whether the split is useful in practice.

### 16. Multi-observer coordination
If two observer Claude sessions run at once, both can call `cancel_task()` etc. No
serialization — mostly fine (idempotent) but edge cases around `boost_ticker` could
double-boost. Low priority.

### 17. X bookmarks poller
Deferred past Monday. Week 2: `~/praxis-inbox/x-bookmarks/` drop directory with X API
exports or browser-extension dumps.

---

## Items that are NOT going to get fixed (by design)

- **No API fallback from CLI.** You explicitly want API mode to require flag + restart.
  Keep it that way.
- **No in-memory queue optimization.** Postgres is the queue. Don't add Redis.
- **No multi-repo.** Monorepo stays.
- **No session resume as a persistence mechanism.** Vault is the memory.
