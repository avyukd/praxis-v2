# Migration plan: autoresearch vault + praxis-copilot → praxis-v2

Draft. Discuss before executing.

## What we're working with

### praxis-autoresearch vault (~260 useful files)

| Source | Count | Assessment | Action |
|---|---|---|---|
| `20_companies/<TICKER>/` | 11 tickers | Only ARGX + CLMT substantive; most are shells | Migrate substantive; archive shells |
| `10_themes/` | 2 (RFS, Hormuz) | Both rich, 158 + 580 lines | Migrate directly |
| `15_concepts/` | 23 | Well-populated, avg 15KB each | Migrate directly |
| `25_people/` | 7 | Stub-like anchors (Buffett, Burry, etc.) | Migrate directly |
| `30_theses/` | 5 | Decision-driven with kill criteria | **Merge into `companies/<TICKER>/thesis.md`** |
| `40_memos/` | 5 (all 2026-04-10) | Recent working cluster | Split: ticker-specific → `companies/<TICKER>/memos/`; cross-cutting → `memos/` |
| `60_questions/` | 47 (46 answered) | High-value Q&A syntheses | Migrate directly |
| `80_sources/` | 160 clipped articles | Hierarchical by date | Migrate to `_raw/desktop_clips/<date>/` |
| `70_signals/sweep_config.yaml` | 1 config file | Monitor policies | **Keep as config, NOT vault** — move to repo or Postgres |
| `90_meta/` state | 6 files (1.5M) | agenda/current_focus/activity/source_index | **Drop — replaced by Postgres tables** |
| `00_inbox/` | 10 captures | Half-thoughts | **Drop or selectively promote to questions** |
| `50_journal/` | 6 daily logs | Embedded transcripts | **Drop — events table is equivalent** |
| `99_development/` | 8 tooling docs | Process notes | Move to `docs/backlog.md` in monorepo |
| `.cache/` | 287MB | Fundamentals + X bookmarks cache | **Drop — regenerable** |
| **Wikilinks** | 1,909 total | Dense graph | **Must rewrite every single one** |

### praxis-copilot data

| Source | Size | Assessment | Action |
|---|---|---|---|
| Local `data/filing_research_state_*.yaml` | 4.4MB, 18 files | Historical filing classifications + decisions | Selective import as `signals_fired` rows |
| Local `data/analyst_state.yaml` | 94KB | Alert triage reactions | Import to `signals_fired` |
| Local `data/queue_state.yaml` | 49KB | GitHub-issue research queue | Review for stuff worth turning into investigations |
| S3 `data/raw/8k/*/analysis.json` | ~100-500MB estimated | LLM analyses of 2K+ filings | **Selective: last 90 days of watchlist tickers only** |
| S3 `data/raw/press_releases/...` | ~500MB-1GB | Raw press releases | **Skip — we have no use for these retrospectively** |
| S3 `data/raw/8k/*/extracted.json` + raw HTML | 1-5GB | Raw filing text | **Skip — re-fetchable from SEC on demand** |
| S3 `data/monitors/*/latest.yaml` | ~10MB | 188 monitor configs + current state | **Skip — v2 monitors work differently** |
| Local telemetry (`.jsonl`) | 19MB | Token usage logs | **Drop** |
| HTML reports, IPC dirs | 7MB | Ephemeral UI | **Drop** |

## Migration principles

1. **Preserve compounding value.** The wiki is months of compile work; that content is the whole point.
2. **Drop operational detritus.** State files, activity logs, cache — v2 regenerates these.
3. **Selective copilot import.** Most historical filings aren't useful; only import what maps to tickers you still care about.
4. **Idempotent + dry-run.** Every step writes a diff first, human-reviews, then applies.
5. **One-shot, not incremental.** We run the migration once into a staging vault, review, swap in. No "gradual migration."
6. **Postgres-native for state.** Anything that was in `90_meta/` goes into DB tables, not files.

## Directory mapping table

| autoresearch path | praxis-v2 path | Notes |
|---|---|---|
| `20_companies/NVDA/notes.md` | `companies/NVDA/notes.md` | Direct |
| `20_companies/NVDA/journal.md` | `companies/NVDA/journal.md` | Direct |
| `20_companies/NVDA/data/` | `companies/NVDA/data/` | Direct |
| `30_theses/nvda-*.md` | `companies/NVDA/thesis.md` | **Merge** — one thesis per company |
| `40_memos/2026-04-10-clmt-*.md` | `companies/CLMT/memos/2026-04-10-*.md` | Re-nest into ticker folder |
| `40_memos/2026-04-10-hormuz-*.md` | `memos/2026-04-10-hormuz-*.md` | Top-level for cross-cutting |
| `40_memos/*-basket-synthesis.md` | `memos/<date>-*.md` | Top-level for cohort memos |
| `10_themes/*.md` | `themes/*.md` | Strip numeric prefix |
| `15_concepts/*.md` | `concepts/*.md` | Strip numeric prefix |
| `25_people/*.md` | `people/*.md` | Keep handle convention |
| `60_questions/*.md` | `questions/*.md` | Direct |
| `80_sources/2026/04/<slug>.md` | `_raw/desktop_clips/2026-04-<dd>/<slug>.md` | Flatten YYYY/MM hierarchy to YYYY-MM-DD |
| `70_signals/sweep_config.yaml` | `/etc/praxis/monitors.yaml` (config, not vault) | Drop from vault entirely |
| `INDEX.md` | Regenerate via `refresh_index` task | Don't carry over |
| `LOG.md` | Start fresh | Don't carry over |
| `00_inbox/` | Drop or promote items to `questions/` | Case by case |
| `50_journal/` | Drop | Postgres events + journal/YYYY-MM-DD handler produces new daily |
| `90_meta/agenda.md` | Drop (Postgres) | Active items become `investigations` |
| `90_meta/current_focus.md` | Drop (Postgres) | Running state is in `tasks` table |
| `90_meta/activity.md` | Drop (Postgres) | Replaced by `events` table |
| `90_meta/source_index.json` | Drop (Postgres) | `sources` table is the new index |
| `90_meta/source_blocklist.json` | `/etc/praxis/source_blocklist.json` | Keep as config |
| `99_development/` | Repo `docs/backlog.md` | Out of vault |
| `.cache/` | Drop entirely | Regenerable |

## Wikilink rewriting — the hardest part

Every one of 1,909 wikilinks needs its path updated. Examples:

- `[[20_companies/NVDA/notes]]` → `[[companies/NVDA/notes]]`
- `[[10_themes/strait-of-hormuz]]` → `[[themes/strait-of-hormuz]]`
- `[[80_sources/2026/04/2026-04-18_ft_hormuz-article]]` → `[[_raw/desktop_clips/2026-04-18/ft-hormuz-article]]`
- `[[30_theses/clmt]]` → `[[companies/CLMT/thesis]]`
- `[[40_memos/2026-04-10-clmt-rfs-rd-saf-memo]]` → `[[companies/CLMT/memos/2026-04-10-clmt-rfs-rd-saf-memo]]`

**Approach:** deterministic rename map derived from the directory mapping, applied via regex over every `.md` file. The migration tool emits a **rename-map JSON** as its first artifact — human reviews it before anything is written.

**Validation:** after rewriting, a pass over the migrated vault runs the same logic as `lint_vault` and must produce **zero broken wikilinks**. If any remain, either the rename map has a gap or a source genuinely didn't migrate (orphan) — both cases surface as migration-report errors.

## Frontmatter normalization

The autoresearch vault's frontmatter is 95% compatible with v2 already:
- `type: company_note|memo|concept|question|thesis|person|theme` ✓ matches v2
- `status:` ✓ matches v2 (some values differ — `final` in v1 memos = `resolved` in v2, needs a tiny map)
- `data_vintage: YYYY-MM-DD` ✓ matches
- `tags:` ✓ matches
- `links:` ✓ matches
- `ticker:` ✓ matches

Fields that v1 has but v2 doesn't: `created_by_focus`, `created_by_tick`, `preliminary_decision`, `scores: {tactical, fundamental}`. **Keep them** — they're harmless extra metadata and preserve historical context.

Fields to add on migration: `migrated_from: autoresearch` + `migrated_at: <iso>` on every file for audit trail.

## Praxis-copilot selective import

**Default policy: skip everything.** The copilot pipeline is being replaced, not imported. Only pull in stuff that has lasting signal value:

1. **`filing_research_state_*.yaml` + `analyst_state.yaml`** → cross-reference against the autoresearch tickers. For any ticker that has a substantive autoresearch note, extract that ticker's filing decisions from the state YAMLs and write them as `signals_fired` rows (with `fired_at = analyzed_at`, `signal_type = "historical_import"`). Gives observer Claude an "I've seen this filing before" trace.

2. **S3 `analysis.json` files** → for each ticker in autoresearch + watchlist, fetch all analyses from the last 90 days, write them to `_analyzed/filings/8k/<accession>/analysis.md` in v2 format. **Rewrite** (not copy) — the v1 schema (`classification`, `magnitude`, `materiality`) doesn't match v2's schema (`event_type`, `urgency`, `specific_claims`). Use a small LLM pass to translate. ~50-200 files, manageable.

3. **`queue_state.yaml`** → manually review. Items marked `status: done` with a `summary` could become closed `questions/`. Items still pending could become `investigations`. But honestly, most will be stale and worth dropping.

4. **Everything else** → skip.

Estimated migrated volume from copilot: **~100-300 files**, dwarfed by the vault content.

## Phased execution plan

**Phase 0 — Snapshot.** Back up both source vaults (autoresearch + any copilot data) to restic before touching anything. Non-negotiable.

**Phase 1 — Dry run the vault migration.**
- Build `praxis-migrate` CLI in `services/migrate/`.
- `praxis-migrate plan --from ~/dev/praxis-autoresearch/vault --to ~/vault-staging`.
- Output: `migration_report.md` with the rename map, every file's planned action (copy/rewrite/skip/merge), every wikilink that would change, every orphan.
- Human reviews. Iterate on mapping logic until report looks clean.

**Phase 2 — Execute vault migration.**
- `praxis-migrate apply --from ~/dev/praxis-autoresearch/vault --to ~/vault-staging`.
- Writes the new vault. Wikilinks rewritten, frontmatter normalized, theses merged into company folders, memos re-nested.

**Phase 3 — Validate.**
- Run `lint_vault` task against staging vault. Must produce zero broken links.
- Run `refresh_index` task. INDEX.md should list everything.
- Manual spot check: open `companies/NVDA/notes.md`, `companies/CLMT/thesis.md`, `themes/strait-of-hormuz.md` in Obsidian — render correctly, backlinks present.

**Phase 4 — Copilot selective import.**
- `praxis-migrate import-copilot --state-dir ~/dev/praxis-copilot/data --s3 s3://praxis-copilot/data/raw/8k --tickers <from autoresearch + watchlist> --since 90d`.
- Fills `_analyzed/filings/8k/<acc>/analysis.md` for the selected tickers.
- Seeds `signals_fired` with historical entries for audit trail.

**Phase 5 — Cutover.**
- Stop v1 pipelines (already not being used).
- Move `~/vault-staging` → `~/vault` (the v2 default path).
- Start v2 services: they pick up the migrated vault, begin emitting new signals into it.

**Phase 6 — Post-cutover watch.**
- First 24 hours: monitor dashboard for broken anything.
- First new 8-K analyze → spot-check that it compiles into the right company note and links back cleanly.
- Run `lint_vault` daily for the first week; fix any drift immediately.

## Tooling to build

Single module `services/migrate/` with sub-commands:

```
praxis-migrate plan            # dry-run; emits migration_report.md
praxis-migrate apply           # executes file moves + rewrites
praxis-migrate import-copilot  # selective copilot data import
praxis-migrate validate        # post-run check (same as lint_vault + extra)
```

Core primitives it needs:

- **Path rewriter** — regex-based wikilink rewriter with the rename map.
- **Frontmatter normalizer** — maps v1 frontmatter → v2 conventions, adds `migrated_from`.
- **Thesis merger** — takes `30_theses/<ticker>-*.md` content, writes to `companies/<TICKER>/thesis.md`, preserves evidence log.
- **Source flattener** — moves `80_sources/YYYY/MM/<file>` → `_raw/desktop_clips/YYYY-MM-DD/<file>`, updates date in path.
- **Copilot translator** — reads v1 analysis.json, prompts Haiku to convert to v2 signals.json schema.

~600-1000 LOC, one weekend of work. Tests use fake vaults to verify the rename map and frontmatter normalization.

## Known risks

1. **Mis-merged theses.** If multiple thesis files per ticker exist (e.g., old + new), merger might clobber. Mitigation: the merger flags any ticker with >1 thesis file as a warning in the dry-run report, human-decides which to keep.

2. **Wikilinks with aliases.** `[[target|display text]]` syntax. Regex must preserve display text.

3. **Questions referencing dropped content.** 60_questions/ frequently links to 90_meta/agenda items. After dropping meta, those backlinks 404. Mitigation: strip dead links from questions during migration (don't silently break).

4. **Source filename slug drift.** `80_sources/2026/04/2026-04-18_ft.com_article-slug.md` has a domain embedded. v2 convention drops that (`_raw/desktop_clips/2026-04-18/article-slug.md`). Decide: keep domain in filename for dedup/provenance, or drop? **My lean: keep in frontmatter, drop from filename.**

5. **Copilot schema translation losing fidelity.** v1's `magnitude: 0.85` → v2's `urgency: "high"` is a lossy mapping. Keep the original magnitude in a `migrated_original: {...}` frontmatter field so nothing's lost forever.

6. **Two different ARGX / CLMT writings.** If autoresearch has a company note and copilot has a filing analysis for the same ticker, they might conflict. The company note wins; the analysis is stored in `_analyzed/` separately and linked to.

7. **The `.cache/x_bookmarks` (280MB) is worth a second look.** It's clipped X content. If any of it is uniquely valuable (vs being re-fetchable), we'd move it to `_raw/x_bookmarks/`. Otherwise drop. Requires you to scan + decide.

## Open questions for you

1. **Watchlist definition** — which tickers should drive the selective copilot import? Is it "whatever's in autoresearch vault" or a separate list you'll provide?
2. **Orphan content** — several of the 11 autoresearch tickers are shells. Migrate them anyway (empty company dir in v2) or drop?
3. **Historical filings — 90 days enough?** Or 180? Or all of last year? Affects migration time and token cost for the schema translation step.
4. **Where does `source_blocklist.json` live** — in the code repo as `praxis_core/config/source_blocklist.json`, or in `/etc/praxis/`? I lean the former since it's not secret.
5. **X bookmarks cache (280MB)** — do you want to even look at it, or drop on sight?
6. **Are there copilot investigations/memos I'm missing?** The audits covered `data/` and S3. Is there a separate Obsidian vault for copilot I missed?

Reply with answers and I'll scope the migrate tool precisely.
