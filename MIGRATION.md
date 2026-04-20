# Migration runbook

How today's vault (`~/vault`) was built from its source systems, and how to
re-run each step. Section E of `OVERNIGHT.md` is the full decision log (D53–D61);
this doc is the operational surface.

## Sources

| Source | Role | Bucket / path |
|---|---|---|
| `praxis-autoresearch/vault` | Prior v1 wiki tree (themes, concepts, people, memos) | `~/dev/praxis-autoresearch/vault` |
| `praxis-copilot/workspace` | Deeply-researched company dirs (notes, thesis, dives, memos) | `~/dev/praxis-copilot/workspace` |
| `praxis-copilot/data/*.yaml` | Copilot live state — analyst capacity, signals fired, rate buckets | `~/dev/praxis-copilot/data` |
| `s3://praxis-copilot/data/raw/filings/` | ~1,500 copilot-analyzed 8-K/10-Q filings with analysis.json | S3 |
| `s3://praxis-copilot/data/raw/press_releases/` | ~3,100 copilot-analyzed PRs (GNW + Newsfile) | S3 |
| `s3://praxis-copilot/data/events/` | Daily event log | S3 |

## One-shot bootstrap (re-runnable)

```bash
cd ~/dev/praxis-v2

# 1. Dry-run the autoresearch + copilot-workspace → vault merge
uv run python -m services.migrate.cli plan \
    --autoresearch-vault ~/dev/praxis-autoresearch/vault \
    --copilot-workspace ~/dev/praxis-copilot/workspace \
    --copilot-data ~/dev/praxis-copilot/data \
    --target ~/vault-staging

# Review the report at ~/vault-staging-migration-plan.md

# 2. Apply to staging
uv run python -m services.migrate.cli apply \
    --autoresearch-vault ~/dev/praxis-autoresearch/vault \
    --copilot-workspace ~/dev/praxis-copilot/workspace \
    --target ~/vault-staging --force

# 3. Cutover (merge into live ~/vault, preserving live data)
uv run python -m services.migrate.cli cutover \
    --staging ~/vault-staging \
    --production ~/vault \
    --merge

# 4. Import copilot's historical analyses from S3
#    (1476 filings + 3127 press + writes sources seen-set so live pollers
#    don't re-triage)
uv run python -m services.migrate.cli import-copilot-filings \
    --vault ~/vault --concurrency 24

# 5. Import copilot's daily event log
uv run python -m services.migrate.cli import-copilot-events --concurrency 32

# 6. Import copilot's analyst-state YAML into signals_fired
uv run python -m services.migrate.cli import-copilot-state \
    --copilot-data ~/dev/praxis-copilot/data --apply

# 7. Validate
uv run python -m services.migrate.cli validate --target ~/vault
```

## Current vault state (as of 2026-04-20)

| Layer | Count |
|---|---|
| Companies (top-level dirs) | 247 |
| Company dives | 706 |
| Company memos | 192 |
| Themes | 2 |
| Concepts | 22 |
| Filings in `_analyzed/` | 1,541 accession dirs |
| Filings in `_raw/` | 1,541 accession dirs |
| Press release analyses in `_analyzed/` | 3,213 |
| Press release raw bodies in `_raw/` | 3,222 |
| `sources` table rows | ~4,900 |
| `signals_fired` rows | 17 |
| `events` rows (historical) | 12 |

**Vault size**: ~215 MB.

## What each step does

### `plan` / `apply`
`services/migrate/vault_migrator.py` handles the autoresearch → new-schema
transformation (`30_theses/` → `companies/<T>/thesis.md`, `40_memos/` re-nested
under companies, seed files from `vault_seed/`, wikilink rewriting). Idempotent
via `--force` on re-run.

`services/migrate/workspace_migrator.py` pulls copilot's per-ticker workspace
dirs into `~/vault/companies/<TICKER>/` — analyst reports move into
`dives/<specialist>.md` per D53, all 7 specialists mapped per D54.

### `cutover --merge`
`rsync -a --ignore-existing` staging → production. Live data (things the running
pollers and dispatcher wrote after the plan was built) wins on conflict.
Append-only audit row in `~/vault/_cutover.log`.

### `import-copilot-filings` (D58)
`services/migrate/copilot_filings.py` enumerates
`s3://praxis-copilot/data/raw/{filings,press_releases}/`, translates
`{classification, magnitude, new_information, materiality, explanation}`
into our `AnalysisResult`, writes `vault/_analyzed/.../analysis.{md,json}`
matching the live `analyze_filing` handler format 1:1. Also writes the
primary doc to `_raw/` and INSERTs a `sources` row with the dedup key so
live pollers see it as already-processed (no re-triage).

Handles both dashed (`0001140361-26-008484`) and undashed
(`000114036126008484`) accession formats from copilot. Defensive
ticker truncation to 16 chars.

### `import-copilot-events` (D59)
`services/migrate/copilot_events.py` loads copilot's daily event files
(`evt-<id>.json`) as rows in our `events` table with
`component='migrate.copilot_events'` and
`event_type='{filing,release}_ingested_historical'`. Dedup on
`payload->>'event_id'` so re-running is free.

### `validate`
`services/migrate/cli.py:validate` scans every `.md` file for `[[wikilink]]`
syntax and checks each target exists in the vault. Indexes all files
(md + json + yaml), not just markdown, so wikilinks to `_analyzed/.../analysis.json`
resolve correctly.

Latest run: 6,025 files / 1,710 wikilinks / **34 broken** (all false-positive
template placeholders or LLM prompt quirks). Down from 237 pre-fix.

## Not imported (by design)

- `s3://8k-scanner-raw/` (42k objects, 2.4 GB): predecessor 8-K scanner raw
  content. Copilot analyses cover the subset worth deep analysis; the scanner's
  broader raw corpus would cost $7,500+ to Sonnet-analyze in bulk. Left
  available on S3 for future targeted pulls (e.g., when a dive needs a
  specific historical filing).
- Live-pipeline events from before the Monday start: the historical events
  bucket was small (12 events).

## Known quirks

- LLM-generated dives sometimes use wikilink syntax for web searches:
  `[[WebSearch: query text]]`. Cosmetic; validator flags these as broken.
  Prompt-level cleanup is a nice-to-have, not blocking.
- Memo links to `_analyzed/press_releases/.../<accession>` (directory, not
  `/analysis.json` file) also register as broken. Not a data gap — the
  analysis files exist; the wikilink just drops the suffix.

## Re-running safely

Every step above is idempotent:
- `apply` with `--force` re-writes staging
- `cutover --merge` is `rsync --ignore-existing` (never clobbers)
- `import-copilot-filings` checks file existence on disk + `sources` row before
  writing
- `import-copilot-events` dedupes on `payload->>'event_id'`
- `import-copilot-state` is upsert-style in Postgres

Safe to re-run any of these against the live vault.
