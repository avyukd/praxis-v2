"""Deterministic 8-K item-code filter.

Ported from praxis-copilot/src/modules/events/eight_k_scanner/extract/filter.py.
Applied at the poller before any LLM call. Drops filings whose items don't match
the allowlist — no cost, no latency.

Item codes are what make 8-Ks meaningful. Most "8-K" volume is noise:
  - 7.01 Regulation FD Disclosure (often just reaffirmation)
  - 9.01 Financial Statements and Exhibits (only material if paired with other items)
  - 5.03 Amendments to Articles of Incorporation or Bylaws (usually housekeeping)

The LONG allowlist captures "positive catalyst" items: material agreements, earnings,
insider transactions, board changes, NDAs, etc.

Extraction from the atom feed's <summary>:
    "<b>Filed:</b> 2026-04-17 <b>AccNo:</b> X <b>Size:</b> Y
     <br>Item 3.01: Notice of Delisting ...
     <br>Item 9.01: Financial Statements and Exhibits"

We parse "Item N.NN" patterns out of the summary HTML. This avoids needing to download
the filing itself to classify it.
"""

from __future__ import annotations

import re

# LONG-bias allowlist: items that typically indicate a positive or material catalyst.
# Matches copilot's LONG_ITEMS plus a few additions useful for signal surfacing.
LONG_ITEMS: frozenset[str] = frozenset(
    {
        "1.01",  # Entry into a Material Definitive Agreement
        "2.01",  # Completion of Acquisition/Disposition of Assets
        "2.02",  # Results of Operations and Financial Condition (earnings)
        "2.03",  # Creation of a Direct Financial Obligation
        "2.04",  # Triggering Events That Accelerate a Direct Financial Obligation
        "2.05",  # Costs Associated with Exit or Disposal Activities
        "2.06",  # Material Impairments
        "3.02",  # Unregistered Sales of Equity Securities
        "4.01",  # Changes in Registrant's Certifying Accountant
        "4.02",  # Non-Reliance on Previously Issued Financial Statements
        "5.01",  # Changes in Control of Registrant
        "5.02",  # Departure/Appointment of Directors or Principal Officers
        "5.06",  # Change in Shell Company Status
        "7.01",  # Regulation FD Disclosure
        "8.01",  # Other Events
    }
)

# Items that are usually noise-only unless paired with something above.
_IGNORE_SOLO_ITEMS: frozenset[str] = frozenset(
    {
        "9.01",  # Financial Statements and Exhibits (cover for other items)
        "3.01",  # Notice of Delisting (negative, handled separately if we ever add SHORT)
        "5.03",  # Amendments to Articles / Bylaws (housekeeping)
        "5.07",  # Submission of Matters to a Vote (annual meeting results)
    }
)


_ITEM_RE = re.compile(r"Item\s+(\d{1,2}\.\d{1,2})\b", re.IGNORECASE)


def extract_items_from_summary(summary_html: str) -> list[str]:
    """Pull every 'Item N.NN' pattern out of EDGAR's atom <summary>.

    Preserves order and de-duplicates while keeping first occurrence.
    Returns an empty list if no items found.
    """
    if not summary_html:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in _ITEM_RE.finditer(summary_html):
        code = match.group(1)
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


def items_pass_allowlist(
    items: list[str], *, allowlist: set[str] | frozenset[str] | None = None
) -> tuple[bool, set[str]]:
    """Check whether a filing's detected items pass the allowlist.

    Returns (passes, matched_items). `passes` is True iff at least one item
    appears in the allowlist.
    """
    if allowlist is None:
        allowlist = LONG_ITEMS
    matched = {i for i in items if i in allowlist}
    return bool(matched), matched
