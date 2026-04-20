"""Tests for the dive-quality refactor validator strictness."""

from __future__ import annotations

from pathlib import Path

from praxis_core.tasks.validators import (
    _check_research_depth,
    _check_word_budget,
    validate_dive_business_moat,
    validate_dive_financial_rigorous,
)


def _write_dive(tmp_path: Path, ticker: str, slug: str, content: str) -> Path:
    p = tmp_path / "companies" / ticker / "dives" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


PAYLOAD_P5 = {"ticker": "BTO", "investigation_handle": "bto-x", "research_priority": 5}
PAYLOAD_P2 = {"ticker": "BTO", "investigation_handle": "bto-x", "research_priority": 2}


def _good_dive_body(specialty: str, word_count: int = 200) -> str:
    """A dive that satisfies every quality check: header, retrieval evidence,
    wikilinks, Sources consulted section, INVESTABILITY (if financial)."""
    body = (
        f"---\ntype: dive\nspecialist: {specialty}\nticker: BTO\ndata_vintage: 2026-04-20\n---\n\n"
        f"# BTO — {specialty}\n\n"
        "## Verdict\nUsed `mcp__fundamentals__company_overview(BTO.TO)` to ground this analysis. "
        "Also pulled `mcp__fundamentals__get_full_statement(BTO.TO, income, annual, 4)` and the "
        "primary filing via WebFetch(https://www.sec.gov/cgi-bin/browse-edgar?CIK=BTO&type=40-F).\n\n"
        "Quantitative evidence: [[_raw/filings/40-f/0001234567-26-000001/filing.txt]] confirms the "
        "data.\n\n" + ("Body content. " * word_count) + "\n\n"
        "## Sources consulted\n"
        "- `mcp__fundamentals__company_overview(BTO.TO)` → marketCap=$X.XB\n"
        "- `mcp__fundamentals__get_full_statement(BTO.TO, income, annual, 4)` → FY2024 revenue $Y\n"
        "- `WebFetch(https://www.sec.gov/cgi-bin/browse-edgar?CIK=BTO&type=40-F)` → reserves\n"
        "- `[[_analyzed/press_releases/gnw/BTO/gnw-3276829/analysis.json]]` → press release\n"
    )
    return body


def _reward_hack_body(specialty: str) -> str:
    """A dive that tries to slip through with meta-commentary on data-scarcity
    but never actually called any tool."""
    return (
        f"---\ntype: dive\nspecialist: {specialty}\nticker: BTO\n---\n\n"
        f"# BTO — {specialty}\n\n"
        "## Verdict\nData-limited. The vault contains no 10-K, no fundamentals snapshot. "
        "A proper analysis cannot be produced. Not evaluable. Cannot assess.\n\n"
        "## Cash flow\nNot evaluable.\n\n"
        "## Balance sheet\nNot evaluable.\n\n"
        "## Valuation\nNot producible.\n\n"
        + ("More filler prose that looks thoughtful but has no primary data. " * 40)
    )


# -- research depth --


def test_research_depth_passes_with_retrieval_and_sources():
    content = _good_dive_body("financial-rigorous")
    issues = _check_research_depth("/x/financial-rigorous.md", content)
    assert issues == []


def test_research_depth_rejects_missing_sources_section():
    # Has tool markers but no ## Sources consulted header
    content = (
        "some dive\n"
        "called mcp__fundamentals__company_overview(NVDA)\n"
        "called mcp__fundamentals__get_price(NVDA)\n"
        "used WebFetch(https://sec.gov/...)\n"
    )
    issues = _check_research_depth("/x/dive.md", content)
    assert any("Sources consulted" in i.reason for i in issues)


def test_research_depth_rejects_reward_hack_body():
    content = _reward_hack_body("financial-rigorous")
    issues = _check_research_depth("/x/dive.md", content)
    # Either missing sources section OR insufficient retrieval — at least one
    assert issues
    msgs = " ".join(i.reason for i in issues)
    assert "Sources consulted" in msgs or "insufficient" in msgs


def test_research_depth_counts_wikilinks_as_retrieval():
    content = (
        "body\n\n"
        "[[_raw/filings/10-k/abc/filing.txt]]\n"
        "[[_analyzed/press_releases/gnw/NVDA/1234/analysis.json]]\n"
        "[[_raw/filings/8-k/def/filing.txt]]\n\n"
        "## Sources consulted\n"
        "- primary filings cited via wikilinks above\n"
    )
    issues = _check_research_depth("/x/dive.md", content)
    assert issues == []


# -- word budget --


def test_word_budget_passes_within_cap():
    # P5 ≈ 1500 words; 1.3x cap = 1950
    content = "word " * 1500
    issues = _check_word_budget("/x/dive.md", content, 5)
    assert issues == []


def test_word_budget_rejects_over_cap():
    # P2 (Quick Screen) = 500 words, 1.3x = 650. 1200 is way over.
    content = "word " * 1200
    issues = _check_word_budget("/x/dive.md", content, 2)
    assert issues
    assert "exceeds word budget" in issues[0].reason


def test_word_budget_scales_with_priority():
    # Same 3000-word body: fails P5 (1500×1.3=1950) but passes P10 (4000×1.3=5200)
    content = "word " * 3000
    assert _check_word_budget("/x/dive.md", content, 5)
    assert _check_word_budget("/x/dive.md", content, 10) == []


# -- end-to-end validator wiring --


def test_validate_business_moat_full_happy_path(tmp_path):
    _write_dive(tmp_path, "BTO", "business-moat", _good_dive_body("business-moat"))
    r = validate_dive_business_moat(PAYLOAD_P5, tmp_path)
    assert r.is_success, r.malformed


def test_validate_business_moat_reward_hack_rejected(tmp_path):
    _write_dive(tmp_path, "BTO", "business-moat", _reward_hack_body("business-moat"))
    r = validate_dive_business_moat(PAYLOAD_P5, tmp_path)
    assert not r.is_success
    assert r.malformed


def test_validate_financial_rigorous_requires_investability_line(tmp_path):
    # Good body but no INVESTABILITY line
    content = _good_dive_body("financial-rigorous")
    _write_dive(tmp_path, "BTO", "financial-rigorous", content)
    r = validate_dive_financial_rigorous(PAYLOAD_P5, tmp_path)
    assert not r.is_success
    assert any("INVESTABILITY" in m.reason for m in r.malformed)


def test_validate_financial_rigorous_happy_with_verdict(tmp_path):
    content = _good_dive_body("financial-rigorous") + (
        "\n\nINVESTABILITY: CONTINUE — balance sheet clean, cash runway >4 quarters\n"
    )
    _write_dive(tmp_path, "BTO", "financial-rigorous", content)
    r = validate_dive_financial_rigorous(PAYLOAD_P5, tmp_path)
    assert r.is_success, r.malformed


def test_validate_financial_rigorous_stop_verdict_ok(tmp_path):
    content = _good_dive_body("financial-rigorous") + (
        "\n\nINVESTABILITY: STOP — auditor resigned, qualified opinion\n"
    )
    _write_dive(tmp_path, "BTO", "financial-rigorous", content)
    r = validate_dive_financial_rigorous(PAYLOAD_P5, tmp_path)
    assert r.is_success, r.malformed
