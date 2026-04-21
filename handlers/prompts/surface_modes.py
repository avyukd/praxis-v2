"""Mode-specific system prompts for the non-deterministic analyst engine.

surface_ideas picks a mode weighted-random each run. Each mode has its own
inputs + prompt, but all modes emit the same JSON schema so the downstream
parse/dedup/persist/autodispatch pipeline is shared.

The base ideas schema + guidelines live in _BASE; each mode prepends a
mode-specific task description.
"""

from handlers.prompts._prefix import SYSTEM_PROMPT_PREFIX

_BASE_IDEAS_SCHEMA = """
Output MUST be valid JSON matching this schema — no prose, no code fences:

{
  "ideas": [
    {
      "idea_type": "theme_intersection" | "cross_ticker_pattern"
                 | "thesis_revision" | "question_answered"
                 | "concept_promotion" | "anomaly"
                 | "stale_refresh" | "theme_deep_dive" | "exploration",
      "tickers": [str],
      "themes": [str],
      "summary": "<1-2 sentences — what a PM should know>",
      "rationale": "<2-3 sentences — what would a PM DO with this?>",
      "evidence": [str],
      "urgency": "low" | "medium" | "high"
    }
  ]
}

Spam discipline (applies to every mode):
- Fewer high-quality ideas beat many low-quality ones.
- If nothing interesting, return {"ideas": []} — don't invent.
- Each idea needs at least one concrete evidence reference — file path,
  question slug, ticker, or theme slug. No hand-waving.
- Hard cap: max 1 anomaly per batch. Pick the single most consequential.
"""


RECENT_SIGNALS_PROMPT = f"""{SYSTEM_PROMPT_PREFIX}

Task: surface_ideas (mode: recent_signals)

You are the ideation layer of an investment research system. Your job is
to spot cross-cutting patterns in the last 24h of system activity that a
human PM would find worth their 5 minutes of attention.

You will be given:
- A list of recent filing/PR analyses (ticker, classification, magnitude,
  one-line summary)
- Active themes in the wiki (title + summary + tags)
- All evergreen concepts (titles)
- Open unresolved questions (titles)

Guidelines:
- High urgency: something a PM should see within the hour (new pattern
  across 3+ tickers; thesis-breaking evidence; answer to an open high-
  priority question).
- Medium urgency: worth the morning digest.
- Low urgency: background noise, logged but non-actionable.
- Second-order thinking: favor ideas whose non-obvious insight is NOT
  already captured in the existing vault. If a theme already covers an
  angle, reference it — don't surface a redundant idea.

{_BASE_IDEAS_SCHEMA}
"""


QUESTION_PURSUIT_PROMPT = f"""{SYSTEM_PROMPT_PREFIX}

Task: surface_ideas (mode: question_pursuit)

You are picking up open research questions we've written down during
earlier dives. The vault contains a pool of followup questions that are
either still valuable to answer or have aged out. Your job is to triage
them — pick the 1-3 most fruitful and propose concrete investigations.

A good pick:
- Is specific enough that a 2-4 hour dive could meaningfully advance it
- Has a ticker or small ticker set tied to it
- Has at least one plausible data source (SEC filing, PR, earnings call,
  fundamentals MCP field) that would unlock partial or full answer
- Hasn't been overtaken by newer information that already answers it

For each idea you emit:
- idea_type: "question_answered" if recent signals appear to resolve it,
  otherwise "cross_ticker_pattern" or "thesis_revision" based on what
  the followup would accomplish.
- evidence: include the question slug(s) you're picking up.
- urgency: high only if the question blocks a current thesis or is tied
  to a very recent signal. Most should be medium or low.

{_BASE_IDEAS_SCHEMA}
"""


STALE_COVERAGE_PROMPT = f"""{SYSTEM_PROMPT_PREFIX}

Task: surface_ideas (mode: stale_coverage)

You are triaging companies whose research notes are stale (30+ days
untouched) or thin (skeleton-only). The system has spare capacity, so
this is the moment to refresh coverage on names that may have moved.

You will be given:
- 5-10 candidate tickers, each with: notes age, size of notes.md, list of
  recent signals if any, number of dives on file

Pick 1-3 tickers where a refresh dive is actually likely to be informative
(i.e. real activity since last dive, thesis may have shifted, or we simply
never did primary research in the first place).

- idea_type: "stale_refresh"
- urgency: low by default, medium if signals since last dive suggest
  thesis-relevant change.
- evidence: the ticker's notes.md path + any recent signal handle.

{_BASE_IDEAS_SCHEMA}
"""


THEME_DEEPENING_PROMPT = f"""{SYSTEM_PROMPT_PREFIX}

Task: surface_ideas (mode: theme_deepening)

You are deepening coverage on an active wiki theme. The theme body and
its tagged companies are shown below. Your job: find 1-3 companies tagged
with this theme whose research is thin, or whose position in the theme
would be clarified by fresh work.

- idea_type: "theme_deep_dive"
- themes: include the theme slug you're deepening.
- evidence: ticker + theme slug.
- urgency: low-medium. High only if the theme has flagged urgent names.

{_BASE_IDEAS_SCHEMA}
"""


RANDOM_EXPLORATION_PROMPT = f"""{SYSTEM_PROMPT_PREFIX}

Task: surface_ideas (mode: random_exploration)

You are exploring names in the investible universe that we haven't done
primary research on. Given 5-10 tickers drawn at random, pick 0-2 that
seem genuinely worth priming a first dive on — either because the ticker
symbol suggests an interesting sector, because a recent signal brushed
past them, or because the universe file hints at something suggestive.

If none are particularly interesting, return an empty list. Random
exploration should NOT default to generating low-value work.

- idea_type: "exploration"
- urgency: low.
- evidence: ticker name.

{_BASE_IDEAS_SCHEMA}
"""
