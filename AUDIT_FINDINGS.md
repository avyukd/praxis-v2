# Audit findings (action items for Avyuk)

## Open

(none)

## Resolved

### F1+F2 — root cause found & fixed 2026-04-20 09:42 PT: systemd `WatchdogSec=120` in `praxis-dispatcher.service`
Both F1 (artifact-missing DLs in dive/synthesize handlers) and F2 (attempts inflation beyond `max_attempts=3`) were caused by the same upstream issue: the dispatcher systemd unit had `WatchdogSec=120`, but `services/dispatcher/main.py` does not send `sd_notify(WATCHDOG=1)` pings. Systemd was SIGABRTing the dispatcher every 2 minutes, which:
- Killed in-flight Opus dive handlers mid-LLM-call before they could write their output `.md` file → F1 "artifacts missing" DLs
- Allowed the stale `status=running, lease still fresh` rows to be re-claimed on next dispatcher start, incrementing `attempts` on each claim with no handler path ever reaching `_handle_failure` → F2 `attempts=10 >> max_attempts=3`

Evidence in journal: `Apr 20 08:59:57 ... praxis-dispatcher.service: Watchdog timeout (limit 2min)! ... Killing process 27580 (python) with signal SIGABRT`. Restart counter reached 11+ by 09:00 PT.

**Fix applied:** removed `WatchdogSec=120` from both `infra/systemd/praxis-dispatcher.service` (source) and `/etc/systemd/system/praxis-dispatcher.service` (installed); `systemctl daemon-reload` + `restart`. Dispatcher now stable past the 2-min window (verified Monday 09:42-09:45 PT).

**Cleanup applied:** manually dead-lettered the 2 stuck `dive_business_moat` tasks (attempts=10, OCS + BTO) — these would never succeed with the buggy behavior and would keep being reclaimed.

**Follow-ups for future sessions (NOT blocking Monday open):**
- Implement `sd_notify(WATCHDOG=1)` pings in `services/dispatcher/main.py` so the watchdog can be safely re-enabled (currently the dispatcher has no systemd-level liveness gate other than `Restart=always`).
- Consider adding a `attempts < max_attempts` check inside `claim_next_task` as a belt-and-suspenders against any future path that leaves a task in `queued`/`partial` status after a crash — the current `_handle_failure` check is the only guard.
- The 3 artifact-missing DLs from earlier in the day were actually victims of this bug, not prompt failures. The `business_moat` / `industry_structure` prompts still reference stale `data/filings/10-K/*/item*.txt` paths from copilot (non-fatal since the LLM adapts to `_analyzed/`) — worth cleaning up later but not causative.
