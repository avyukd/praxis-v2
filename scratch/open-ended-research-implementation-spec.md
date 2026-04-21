# Full Autonomous Open-Ended Research Plan

## Objective

Make `praxis-v2` capable of true end-to-end AI deep research from a single broad prompt.

Target user experience:

- user gives one prompt such as:
  - "research the strait of hormuz its impact on fertilizer and the best companies to buy"
  - "research AI data center power bottlenecks and the best public beneficiaries"
  - "research uranium enrichment bottlenecks and what equities are most exposed"
- the system autonomously:
  - decomposes the topic
  - gathers sources from the open web
  - persists those sources into the vault
  - creates or updates themes, concepts, questions, and investigations
  - screens candidate companies
  - deep-dives the best names
  - writes a final cross-cutting memo
  - leaves the wiki materially better than before

This plan assumes Claude can already do autonomous web retrieval via:

- `WebSearch`
- `WebFetch`
- `Bash(curl:*)`

That capability already exists in the current worker runtime for research tasks.

## Core Conclusion

The missing piece is not web access. The missing piece is an autonomous research architecture above the existing ticker dive engine.

Today:

- the system is strong at ticker deep dives
- the system is weak at broad-topic research orchestration

So the right approach is:

1. keep the existing ticker deep-dive pipeline as the leaf execution engine
2. add a new autonomous cross-cutting planner/executor layer above it

## What Exists Today

The repo already has useful primitives:

- durable investigations and tasks
- scheduler + dispatcher
- company deep dives
- a vault model with `themes/`, `concepts/`, `questions/`, `memos/`, `investigations/`
- idea surfacing and followup-question generation

The main limitation is scope shape.

The active execution model assumes:

- a `ticker`
- `companies/<TICKER>/notes.md`
- `companies/<TICKER>/dives/*`
- a company memo as the synthesis endpoint

That works for:

- "deep dive CLMT"
- "research NVDA"

It does not naturally work for:

- "research the Strait of Hormuz"
- "research fertilizer exposure from Hormuz"
- "what are the best listed beneficiaries"
- "compare these five beneficiaries and rank them"

## Why Autoresearch Worked Better For This

`praxis-autoresearch` treated the wiki as the primary artifact and treated a focus as a general research topic:

- ticker
- theme
- concept
- question
- basket

It also had a better source persistence loop and explicit query/question handling.

That made it good at:

- branching from a theme to a basket
- promoting subquestions
- answering a narrow question without requiring a full ticker dive
- writing cross-cutting memos that were not single-company artifacts

`praxis-v2` has the stronger worker and orchestration substrate, but the broader research lifecycle was not rebuilt on top of it.

## End-State Requirements

For this to count as true autonomous AI deep research, the system needs all of the following:

1. A freeform research entrypoint
2. Topic decomposition into explicit subquestions and workstreams
3. Autonomous web retrieval
4. Durable source persistence into the vault
5. Theme/question/basket artifact creation and updating
6. Candidate-company discovery and ranking
7. Selective company deep dives
8. Cross-cutting synthesis into a top-level memo
9. Followup generation and recursive continuation when gaps remain
10. Ongoing refresh and maintenance for non-company nodes

If any one of those is missing, the result is AI-assisted research, not full autonomous deep research.

## Architecture Overview

Introduce a new layer above the existing company dive flow.

### Layer 1: Open-ended research orchestration

New responsibilities:

- interpret broad prompt
- classify scope
- generate research graph
- manage source acquisition
- create/update theme and question notes
- shortlist names
- dispatch company leaf work
- synthesize final answer

### Layer 2: Existing company deep dives

Keep current responsibilities:

- rigorous ticker diligence
- independent specialist views
- company memo production

The company engine remains a leaf system. It should not be generalized into a universal planner.

## New Top-Level Workflow

Canonical flow:

1. user submits freeform research prompt
2. system creates a generalized investigation
3. planner decomposes the topic
4. planner identifies research tracks:
   - theme
   - concepts
   - subquestions
   - candidate companies
5. retrieval workers gather and persist evidence
6. compiler workers update theme/question/wiki nodes
7. screening worker ranks candidate equities
8. top names are sent to existing ticker deep-dive flow
9. cross-cutting synthesis writes final top-level memo
10. unresolved gaps become followup questions for future research

## Proposed New Task Types

Add these task types:

- `orchestrate_research`
- `gather_sources`
- `compile_research_node`
- `answer_question`
- `screen_candidate_companies`
- `synthesize_crosscut_memo`
- optional later: `refresh_research_node`

Recommended models:

- `orchestrate_research`: `sonnet`
- `gather_sources`: `sonnet`
- `compile_research_node`: `sonnet`
- `answer_question`: `sonnet`
- `screen_candidate_companies`: `sonnet`
- `synthesize_crosscut_memo`: `opus`

Do not replace:

- `orchestrate_dive`
- `dive_*`
- `synthesize_memo`

Those remain company-specific.

## New Entry Point

Add a first-class MCP tool:

- `research_query(prompt: str, research_priority: int = 5)`

Behavior:

1. generate investigation handle
2. create `Investigation` row with generalized scope
3. write `investigations/<handle>.md`
4. enqueue `orchestrate_research`

This should become the user-facing endpoint for broad research prompts.

Keep `open_investigation()` for explicit company or theme opens, but it should not be the main interface for open-ended work.

## Scope Model

The system needs a first-class scope model that is not ticker-only.

Suggested scope types:

- `company`
- `theme`
- `basket`
- `question`
- `crosscutting`
- optional later: `concept`

### Scope semantics

- `company`: one ticker, existing company dive flow
- `theme`: one macro/industry/narrative thread, e.g. Hormuz
- `basket`: one comparative cohort or ranked set of names
- `question`: a narrow answerable subquestion
- `crosscutting`: larger research thread touching multiple themes and companies

## Payload Changes

Add generalized payloads instead of mutating the ticker-specific ones into awkward unions.

Suggested shapes:

```python
class OrchestrateResearchPayload(BaseModel):
    prompt: str
    investigation_handle: str
    research_priority: int = 5
    scope_type: Literal["company", "theme", "basket", "question", "crosscutting"] | None = None
    subject: str | None = None
    themes: list[str] = []
    concepts: list[str] = []
    questions: list[str] = []
    tickers: list[str] = []
    entry_nodes: list[str] = []


class GatherSourcesPayload(BaseModel):
    investigation_handle: str
    subject: str
    queries: list[str]
    related_nodes: list[str] = []


class CompileResearchNodePayload(BaseModel):
    investigation_handle: str
    node_type: Literal["theme", "concept", "question", "basket"]
    node_slug: str
    subject: str
    source_paths: list[str] = []
    related_nodes: list[str] = []
    tickers: list[str] = []


class AnswerQuestionPayload(BaseModel):
    investigation_handle: str
    question_slug: str
    research_priority: int = 5


class ScreenCandidateCompaniesPayload(BaseModel):
    investigation_handle: str
    subject: str
    tickers: list[str]
    ranking_question: str


class SynthesizeCrosscutMemoPayload(BaseModel):
    investigation_handle: str
    memo_handle: str
    subject: str
    themes: list[str] = []
    concepts: list[str] = []
    questions: list[str] = []
    tickers: list[str] = []
```

## Investigation Model

The DB model can support most of this without immediate migration.

Already usable fields:

- `scope`
- `hypothesis`
- `entry_nodes`
- `artifacts`
- `vault_path`

MVP approach:

- store generalized scope in `scope`
- store richer context in investigation markdown frontmatter and task payloads

Later enhancement if needed:

- add `metadata JSONB` to `investigations`

Do not block implementation on that migration.

## Resource Keys and Concurrency

Current task locking only understands company and investigation scope well enough.

Need new resource key families:

- `theme:<slug>`
- `basket:<slug>`
- `question:<slug>`
- `concept:<slug>`
- `crosscutting:<slug>`

Use them for:

- `compile_research_node`
- `answer_question`
- `synthesize_crosscut_memo`

This avoids:

- two tasks editing the same theme file
- two planners racing to answer the same question
- overlapping cross-cutting memo writes

## Autonomous Retrieval

Claude already has direct retrieval ability. The system should explicitly lean on that.

### Retrieval policy

Workers should be allowed to:

- use `WebSearch` for discovery
- use `WebFetch` for initial reads
- use `curl` for deterministic fetches when needed

The problem is not "can Claude search the web?"

The problem is "how do those web findings become persistent, reusable research memory?"

## Persistent Source Strategy

Even with direct web retrieval, durable research still requires source persistence.

### Requirement

Any source that materially informs:

- a theme note
- a question answer
- a basket comparison
- a final memo

must be persisted into the vault.

### Storage target

Use:

- `_raw/manual/<YYYY-MM-DD>/<slug>.md`

Each persisted source should capture:

- URL
- title
- site
- fetch time
- publish date if known
- cleaned body excerpt or full clipped text

### Implementation options

Option A:

- port `clip`, `clip_text`, `search_sources` from autoresearch

Option B:

- add a slimmer `persist_web_source(url, fetched_text, metadata)` primitive to `praxis-v2`

Recommendation:

- do the slimmer v2-native implementation unless the old code ports cleanly

Do not require the exact old MCP API. Do require the old behavior:

- web findings become durable local sources
- future research can search the local corpus first

## Research Graph Artifacts

The planner should explicitly create a graph, not just a memo.

### Theme nodes

Example:

- `themes/strait-of-hormuz.md`

Should contain:

- thesis
- relevance
- channels of impact
- linked industries
- linked commodities
- linked questions
- linked candidate beneficiaries
- evidence section
- related section

### Question nodes

Questions are critical. Most open-ended prompts decompose into multiple narrower questions.

Example:

- `questions/hormuz-fertilizer-transmission.md`
- `questions/which-fertilizer-companies-benefit-most-from-hormuz.md`
- `questions/which-hormuz-beneficiaries-are-already-priced-in.md`

Each question should have:

- `status: open | partial | answered`
- why it matters
- what would answer it
- answer body when available
- evidence
- related nodes

### Basket notes

When the prompt is comparative, create a basket note or comparative workspace.

Possible paths:

- `memos/<date>-hormuz-fertilizer-beneficiaries.md` for final output
- optional later: `baskets/<slug>.md` if a first-class directory is desired

For now, top-level `memos/` plus theme/question nodes are sufficient.

### Concept nodes

If a recurring mechanism emerges across themes or baskets, create/update `concepts/<slug>.md`.

Example:

- `concepts/chokepoint-economics.md`

Do not force concept creation on every query. Promote only when recurrence is real.

## Research Planner Behavior

### `orchestrate_research`

This is the key new task.

Inputs:

- freeform prompt
- existing vault search results
- nearby themes/questions/memos/concepts
- recent related company notes if relevant

Responsibilities:

1. classify the prompt
2. identify the main thesis or framing
3. enumerate subquestions
4. identify likely relevant themes/concepts
5. identify candidate companies if applicable
6. produce a work plan
7. enqueue retrieval, compile, screening, and synthesis tasks
8. write investigation plan to `investigations/<handle>.md`

Output should be structured, ideally JSON or tightly normalized markdown.

Suggested output shape:

```json
{
  "scope_type": "theme",
  "subject": "Strait of Hormuz fertilizer impact and public equity beneficiaries",
  "theme_nodes": [
    {"slug": "strait-of-hormuz", "action": "update"}
  ],
  "question_nodes": [
    {"slug": "hormuz-fertilizer-transmission", "action": "create"},
    {"slug": "best-fertilizer-equities-for-hormuz", "action": "create"},
    {"slug": "which-beneficiaries-are-priced-in", "action": "create"}
  ],
  "retrieval_queries": [
    "Strait of Hormuz fertilizer exports sulfur phosphate impact",
    "Hormuz closure fertilizer beneficiaries public companies",
    "Persian Gulf sulfur exports phosphate fertilizer producers"
  ],
  "candidate_tickers": ["MOS", "CF", "NTR", "ITFS"],
  "tickers_to_deep_dive": ["MOS", "CF", "ITFS"],
  "final_artifact": {
    "kind": "crosscut_memo",
    "memo_handle": "hormuz-fertilizer-beneficiaries"
  }
}
```

## Retrieval Worker

### `gather_sources`

Purpose:

- execute retrieval queries
- inspect results
- fetch useful pages
- persist useful pages into `_raw/manual/...`
- return source paths

This should be autonomous and evidence-seeking, not just search-result collecting.

Rules:

- prioritize primary and high-quality sources
- avoid low-signal SEO junk
- persist only useful sources
- write a brief retrieval log into the investigation note

For queries like Hormuz/fertilizer, likely source classes:

- trade press
- shipping or commodity data
- government/EIA/USDA sources
- company IR pages
- filings and transcripts

## Node Compiler

### `compile_research_node`

Purpose:

- compile persisted sources into durable theme/question/concept artifacts

This is the non-company analogue of `compile_to_wiki`.

Modes:

- `theme`
- `question`
- `concept`
- optional later `basket`

Responsibilities:

- update or create the node file
- add evidence and related links
- avoid full rewrites unless necessary
- preserve prior useful context

This handler should become the primary way broad-topic research improves the wiki.

## Question Answering

### `answer_question`

Purpose:

- take one explicit research question
- gather needed evidence
- answer it or mark partial

This gives the system a recursive research structure:

- broad prompt
- planner emits subquestions
- questions get answered one by one
- final memo synthesizes across answered questions

This is one of the most important missing pieces from the current repo.

## Candidate Discovery and Screening

### `screen_candidate_companies`

Purpose:

- decide which names are worth real deep dives

This task should:

- rank names by exposure purity
- rank names by investability
- rank names by valuation
- flag which names are already well-covered in the wiki
- recommend:
  - deep dive now
  - note only
  - reject

This prevents:

- opening 15 expensive dives on every theme prompt
- confusing relevance with investability

Example output:

```json
{
  "ranked": [
    {"ticker": "ITFS", "verdict": "deep_dive", "why": "..."},
    {"ticker": "CF", "verdict": "deep_dive", "why": "..."},
    {"ticker": "MOS", "verdict": "note_only", "why": "..."},
    {"ticker": "NTR", "verdict": "reject", "why": "..."}
  ]
}
```

## Reuse of Existing Company Flow

Do not rewrite the company engine.

Once a ticker is selected for true diligence:

- open a normal company investigation
- enqueue existing `orchestrate_dive`
- let current `dive_*` and `synthesize_memo` run unchanged

This keeps:

- company-specific INVESTABILITY gating
- specialist independence
- current validations
- current production reliability

The cross-cutting layer should consume company outputs, not replace them.

## Cross-Cutting Synthesis

### `synthesize_crosscut_memo`

Purpose:

- produce the final answer to the user’s broad prompt
- cite themes, questions, raw sources, and company artifacts
- rank candidate equities when relevant

Output path:

- `memos/<YYYY-MM-DD>-<memo_handle>.md`

Required sections:

- thesis / framing
- what changed or why the topic matters now
- causal chain or transmission map
- evidence summary
- candidate equity ranking
- what is known vs uncertain
- open questions
- related links

This is the missing endpoint for broad research prompts.

## Recursive Continuation

A strong research system should not stop at the first memo if meaningful gaps remain.

After each major task, the system should be able to ask:

- what is unresolved?
- what subquestion matters most now?
- which gaps are answerable with more evidence?

That can generate:

- new `questions/*.md`
- additional `gather_sources`
- follow-on company investigations

This continuation logic should be constrained by budget and priority, but it should exist.

## Maintenance and Refresh

Full autonomous deep research requires a maintenance loop for non-company nodes too.

Need scheduled refresh behavior for:

- active themes
- unanswered or stale questions
- live cross-cutting baskets
- stale top-level memos linked to active themes

Possible future task:

- `refresh_research_node`

That can:

- check source freshness
- refresh core assumptions
- reopen related questions
- enqueue updated screening if needed

This is necessary if the system is going to compound instead of only producing one-shot artifacts.

## Validation Strategy

Current validators are company-heavy. Add lighter but real validation for non-company tasks.

### `compile_research_node` validation

Require:

- file exists
- frontmatter exists
- evidence section exists
- at least one valid source wikilink
- related links resolve

### `answer_question` validation

Require:

- question file exists
- status is updated
- answer body or explicit partial rationale exists
- citations exist

### `synthesize_crosscut_memo` validation

Require:

- memo exists
- frontmatter exists
- links to themes/questions/sources exist
- if equities are ranked, rankings are actually justified in the body

Do not overcomplicate this initially. The main goal is to ensure evidence-backed artifact creation.

## Required File Changes

Likely files to change:

- `praxis_core/schemas/payloads.py`
- `praxis_core/schemas/task_types.py`
- `praxis_core/tasks/enqueue.py`
- `services/mcp/server.py`
- `handlers/__init__.py`
- `handlers/orchestrate_research.py`
- `handlers/gather_sources.py`
- `handlers/compile_research_node.py`
- `handlers/answer_question.py`
- `handlers/screen_candidate_companies.py`
- `handlers/synthesize_crosscut_memo.py`
- `praxis_core/tasks/validators.py`
- optionally `praxis_core/vault/conventions.py`

## Tests to Add

Unit:

- `test_research_query_payloads.py`
- `test_orchestrate_research_parsing.py`
- `test_compile_research_node.py`
- `test_answer_question.py`
- `test_screen_candidate_companies.py`
- `test_synthesize_crosscut_memo.py`

Integration:

- `test_open_ended_research_flow.py`
- `test_theme_to_company_fanout.py`
- `test_question_answering_flow.py`

## Implementation Phases

### Phase 1: Freeform research entrypoint

Build:

- `research_query()` MCP tool
- `TaskType.ORCHESTRATE_RESEARCH`
- `OrchestrateResearchPayload`
- `handlers/orchestrate_research.py`

Success:

- broad prompt creates structured investigation and plan

### Phase 2: Persistent retrieval

Build:

- source persistence helper or `clip`-style primitive
- `TaskType.GATHER_SOURCES`
- `handlers/gather_sources.py`

Success:

- planner can autonomously fetch and persist sources into `_raw/manual/...`

### Phase 3: Theme/question compilation

Build:

- `TaskType.COMPILE_RESEARCH_NODE`
- `handlers/compile_research_node.py`
- node validations

Success:

- broad-topic work updates `themes/` and `questions/` as durable wiki memory

### Phase 4: Question workflow

Build:

- `TaskType.ANSWER_QUESTION`
- `handlers/answer_question.py`

Success:

- broad prompts are decomposed into durable answerable subquestions

### Phase 5: Candidate screening

Build:

- `TaskType.SCREEN_CANDIDATE_COMPANIES`
- `handlers/screen_candidate_companies.py`

Success:

- system can shortlist names before launching expensive deep dives

### Phase 6: Cross-cutting synthesis

Build:

- `TaskType.SYNTHESIZE_CROSSCUT_MEMO`
- `handlers/synthesize_crosscut_memo.py`

Success:

- broad prompts end with a top-level memo that actually answers the query

### Phase 7: Maintenance loop

Build:

- refresh strategy for active themes/questions/baskets

Success:

- the research system compounds and revisits live threads without manual prompting

## MVP For the Strong Standard

If the goal is "as close as possible to true autonomous deep research," the minimal acceptable version is:

1. `research_query`
2. `orchestrate_research`
3. autonomous web retrieval with persistence
4. theme/question node compilation
5. candidate-company screening
6. selective company deep dives
7. top-level synthesis memo

Anything less is still useful, but it is not the standard requested.

## Example End-to-End: Hormuz Prompt

Prompt:

- "research the strait of hormuz its impact on fertilizer and the best companies to buy"

Desired execution:

1. create investigation `hormuz-fertilizer-<ts>`
2. planner classifies as a theme plus basket question
3. retrieval gathers:
   - Hormuz chokepoint context
   - Gulf fertilizer and sulfur export exposure
   - fertilizer value-chain implications
   - public companies with relevant exposure
4. persist useful sources into `_raw/manual/...`
5. update `themes/strait-of-hormuz.md`
6. create and answer:
   - `questions/hormuz-fertilizer-transmission.md`
   - `questions/best-fertilizer-equities-for-hormuz.md`
   - `questions/which-hormuz-beneficiaries-are-priced-in.md`
7. shortlist candidate names
8. launch ticker deep dives on the strongest names
9. write `memos/<date>-hormuz-fertilizer-beneficiaries.md`
10. link everything bidirectionally
11. leave unresolved gaps as open followup questions

## Key Risks

### Risk: trying to force everything into the company pipeline

Bad outcome:

- brittle unions
- degraded reliability in the existing company flow

Mitigation:

- keep cross-cutting handlers parallel to company handlers

### Risk: retrieval without persistence

Bad outcome:

- one-shot answers
- no compounding wiki memory

Mitigation:

- require persistence for material sources

### Risk: planner over-expands work

Bad outcome:

- too many subquestions
- too many ticker dives
- runaway cost

Mitigation:

- explicit caps
- ranking and shortlist stages
- research priority drives breadth

### Risk: final memo outruns the evidence

Bad outcome:

- polished but weakly grounded answers

Mitigation:

- require cited persisted sources
- require explicit known/unknown sections
- validate rankings against body evidence

## Recommendation

Build toward the full standard in this order:

1. freeform planner
2. persistent retrieval
3. theme/question compilation
4. screening bridge
5. top-level synthesis
6. maintenance/refresh loop

That yields a system that is genuinely capable of autonomous broad-topic deep research while preserving the strength of the current ticker deep-dive machinery.

## Acceptance Standard

This project meets the intended bar when the following is true:

Given one broad prompt, the system can autonomously:

- research the topic on the web
- persist useful evidence
- improve the wiki structure
- discover and rank relevant public companies
- deep-dive the best names
- produce an evidence-backed top-level memo

without requiring the user to manually break the topic into ticker-sized tasks.

