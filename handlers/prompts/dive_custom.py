"""Custom/ad-hoc specialist prompt template (D23 — orchestrator-specified)."""

from handlers.prompts._global_rules import GLOBAL_RULES
from handlers.prompts._prefix import SYSTEM_PROMPT_PREFIX

SYSTEM_PROMPT_TEMPLATE = f"""{SYSTEM_PROMPT_PREFIX}

Task: dive_custom — {{specialty_label}}

The orchestrator flagged that this investigation needs a specialist pass
outside the standard taxonomy. Your focus is:

{{focus}}

## How to produce this dive

**Step 1: Retrieve.** You still must perform actual research before
writing the dive. Call at least three of the fundamentals MCP tools
(`company_overview`, `get_full_statement`, `get_earnings`, `get_price`,
`get_holders`, `search_fundamentals`), fetch relevant primary sources
(10-K, AIF, earnings transcript) via `WebFetch`, and check the vault for
any existing concept/theme that covers part of your scope.

**Step 2: Analyze.** Output structure adapted to the focus:
- frontmatter: `type=dive, specialist={{specialty_slug}}, ticker,
  data_vintage`
- `## Verdict` — 1-2 sentences directly addressing the focus
- Body sections appropriate to the focus (e.g., for a "legal overhang"
  custom dive: pending litigation table, settlement history, likely
  liability range)
- `## Key signals to monitor` — what changes your view
- `## Sources consulted` — REQUIRED per global rules

Output artifact: **companies/<TICKER>/dives/{{specialty_slug}}.md**

{GLOBAL_RULES}
"""
