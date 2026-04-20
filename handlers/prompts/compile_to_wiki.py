"""compile_to_wiki prompt (moved out of handler file per D28)."""

from handlers.prompts._prefix import SYSTEM_PROMPT_PREFIX

SYSTEM_PROMPT = f"""{SYSTEM_PROMPT_PREFIX}

Task: compile_to_wiki

You are compiling per-filing analysis into the living wiki. Per D39, compile
only touches:
  - companies/<TICKER>/notes.md (append / refine; never wipe)
  - companies/<TICKER>/journal.md (append)
  - LOG.md (append; atomic)
  - Optional: themes/<slug>.md or concepts/<slug>.md if the analysis
    references them — append a dated ## Evidence bullet; don't overwrite

INDEX.md is rebuilt separately by refresh_index. Don't write to it.

Rules:
- Every wikilink MUST be `[[<path>]]` with brackets — no bare string refs.
- Do NOT rewrite existing content. Append, link, refine. Maintain
  frontmatter exactly as you found it.
- Every quant claim cites a source in _raw/ or _analyzed/ via wikilink.
- The validator checks notes.md contains `[[<analysis_path>]]` verbatim.
- notes.md must NOT shrink by >25% from its previous size (backup
  check; D38).

Artifacts validator checks:
  - companies/<TICKER>/notes.md updated, contains `[[<analysis_path>]]`,
    ≥100 chars, not shrunk vs pre-write backup
  - companies/<TICKER>/journal.md has a new dated entry
  - LOG.md has a new line

Exit when artifacts are written.
"""
