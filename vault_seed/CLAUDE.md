# Vault conventions — read this first

You are an agent writing into a reliability-critical investment research wiki.
This vault is AI-managed and human read-only.

## Layers

- `_raw/` — firehose input. NEVER edit. Read to cite.
  - `filings/{8k,10q,10k}/<accession>/filing.txt + meta.json`
  - `press/YYYY-MM-DD/...`, `x_bookmarks/...`, `desktop_clips/...`, `manual/...`
- `_analyzed/` — per-event first-pass analysis. Write here for triage/analyze results, read when compiling.
- `companies/`, `people/`, `themes/`, `concepts/`, `questions/`, `investigations/`, `memos/` — the wiki proper.
  COMPILE here. Bidirectional wikilinks mandatory.
- `journal/` — machine-generated daily summaries. Append-only.
- Root files: `INDEX.md`, `LOG.md` — auto-maintained.

## Semantics

- **company** (`companies/<TICKER>/`): living compiled knowledge.
  - `notes.md` — running compiled summary (the truth about this company).
  - `thesis.md` — optional evolving thesis with kill criteria.
  - `memos/YYYY-MM-DD-<handle>.md` — dated formal deliverables (Buy/Sell/Neutral decisions).
  - `journal.md` — append-only work log (what was done, when, why).
  - `data/` — structured extracts (fundamentals snapshots, filing tables).
  - `analyst_reports/<specialist>.md` — subagent deep-dives (rigorous-financial, business-moat,
    macro, etc.). Each report is the output of a specialist pass; `notes.md` compiles across them.
- **theme** (`themes/<slug>.md`): time-bound narrative with direction and kill criteria.
  E.g., "ai-capex-digestion", "strait-of-hormuz".
- **concept** (`concepts/<slug>.md`): evergreen framework OR specialty domain knowledge.
  E.g., "circle-of-competence", "d4-rin", "chokepoint-economics".
- **person** (`people/<slug>.md`): execs, fund managers, famous investors.
- **question** (`questions/<slug>.md`): open inquiry. Gets answered later, contents include
  what would answer it and why it matters.
- **investigation** (`investigations/<slug>.md`): multi-task research thread. PM assignment unit.
  Has a plan, log, and resolves via a memo.
- **memo** (top-level `memos/<date>-<handle>.md` OR `companies/<TICKER>/memos/...`):
  dated synthesized deliverable. Decision = Buy | Sell | Neutral | Too Hard. "Too Hard" is valid.

## Rules (non-negotiable)

1. **Every quantitative claim** must cite a primary source via `[[wikilink]]` to `_raw/...`
   or a `[fundamentals: get_financial_data(TICKER), YYYY-MM-DD]` annotation.
2. **Bidirectional wikilinks**: when you add `[[other-note]]` to note A, update note B's
   `## Related` section + `links:` frontmatter to include A.
3. **Every note has YAML frontmatter**: `type, status, data_vintage, tags, links`.
4. **Compile passes are additive**: append, link, refine. Don't wipe prior content.
5. **Source-first ingestion**: for a new ticker, primary sources (10-K/10-Q/transcripts) before secondary.
6. **"I don't know" > guess**. If you can't source an answer, file a question in `questions/`.
7. **Karpathy 10-15 rule**: a compile pass should touch 5-15 pages, not just one. Update INDEX.md
   when adding new nodes.
8. **Never write to `_raw/` or `_analyzed/` as an editor** — only as the producing pipeline component.
9. **Decision hygiene**: most memos should land Neutral or Too Hard. Reserve Buy/Sell for
   genuinely compelling views.

## Workflow (for workers — what you typically do)

- **triage_filing**: write `_analyzed/filings/<form>/<acc>/triage.{md,json}`
- **analyze_filing**: write `_analyzed/filings/<form>/<acc>/{analysis.md,signals.json}`
- **compile_to_wiki**: update `companies/<TICKER>/notes.md` + `journal.md` + `INDEX.md` + `LOG.md`;
  touch affected themes/concepts/people too
- **orchestrate_dive**: write `investigations/<handle>.md` with plan
- **dive_{business,moat,financials}**: update specific section of `companies/<TICKER>/notes.md`
- **synthesize_memo**: write `companies/<TICKER>/memos/<date>-<handle>.md`

## Disallowed sources

Motley Fool, AI blogspam, content farms, unattributed SEO finance blogs, unsourced Reddit/Twitter takes.
SEC filings, primary press (FT, WSJ, Reuters, Bloomberg), company press releases, transcripts, and
reputable trade press are fine. Intelligent investor writeups (VIC, MicroCapClub) are gold.

## Frontmatter template

```
---
type: company_note | thesis | memo | question | source | theme | concept | person | investigation
ticker: NVDA
status: active | paused | done | resolved | answered
data_vintage: 2026-04-18
tags: [tag1, tag2]
links: [other-slug, another-slug]
---
```

## Style

- Lead with findings, not setup. No preambles.
- Tables over prose for comparable data.
- Second-order > first-order. "Margins expanding because of mix shift to segment X, sustainability
  depends on Y" beats "margins are expanding".
- If a sentence adds no insight, delete it.
- Cross-link aggressively. The graph IS the value.
