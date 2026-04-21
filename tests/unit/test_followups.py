"""Tests for praxis_core.vault.followups — dive-reflection question pool."""

from __future__ import annotations

import pytest

from praxis_core.vault.followups import load_open_followups, write_followup


@pytest.fixture
def vault(tmp_path):
    return tmp_path


def test_write_creates_file_with_frontmatter(vault):
    p = write_followup(
        vault,
        title="Does covenant reset under X condition?",
        body="Need to check 10-K footnote + peer disclosures.",
        origin_task_type="dive_financial_rigorous",
        ticker="SWAN",
        investigation_handle="swan-2026-04-20",
        priority="medium",
    )
    assert p is not None
    assert p.exists()
    text = p.read_text()
    assert "type: question" in text
    assert "status: open" in text
    assert "ticker: SWAN" in text
    assert "origin_task_type: dive_financial_rigorous" in text
    assert "origin_investigation: swan-2026-04-20" in text
    assert "priority: medium" in text
    assert "followup" in text  # tag
    assert "Does covenant reset under X condition?" in text
    assert "Need to check 10-K footnote" in text


def test_dedup_by_title_ticker_hash(vault):
    """Same (title, ticker) shouldn't create two files."""
    p1 = write_followup(
        vault,
        title="Repeated question",
        body="Body A",
        origin_task_type="dive_business_moat",
        ticker="ABC",
    )
    p2 = write_followup(
        vault,
        title="Repeated question",
        body="Body B (different but same title+ticker)",
        origin_task_type="dive_business_moat",
        ticker="ABC",
    )
    assert p1 is not None
    assert p2 is None  # deduped


def test_different_tickers_same_title_both_persist(vault):
    p1 = write_followup(
        vault,
        title="How does pricing power compare?",
        body="Context",
        origin_task_type="dive_business_moat",
        ticker="ABC",
    )
    p2 = write_followup(
        vault,
        title="How does pricing power compare?",
        body="Context",
        origin_task_type="dive_business_moat",
        ticker="XYZ",
    )
    assert p1 is not None
    assert p2 is not None
    assert p1 != p2


def test_load_open_followups_returns_fresh_questions(vault):
    write_followup(
        vault,
        title="Q1",
        body="Body 1",
        origin_task_type="dive_business_moat",
        ticker="ABC",
    )
    write_followup(
        vault,
        title="Q2",
        body="Body 2",
        origin_task_type="dive_capital_allocation",
        ticker="XYZ",
    )
    fups = load_open_followups(vault, limit=10)
    titles = {f["title"] for f in fups}
    assert "Q1" in titles
    assert "Q2" in titles


def test_load_open_followups_excludes_resolved(vault):
    import frontmatter

    p = write_followup(
        vault,
        title="Resolved question",
        body="Context",
        origin_task_type="dive_macro",
        ticker="FOO",
    )
    assert p is not None
    post = frontmatter.load(str(p))
    post.metadata["status"] = "resolved"
    p.write_text(frontmatter.dumps(post))

    fups = load_open_followups(vault, limit=10)
    titles = {f["title"] for f in fups}
    assert "Resolved question" not in titles


def test_load_open_followups_limit(vault):
    for i in range(5):
        write_followup(
            vault,
            title=f"Question {i}",
            body=f"Body {i}",
            origin_task_type="dive_business_moat",
            ticker=f"T{i}",
        )
    fups = load_open_followups(vault, limit=3)
    assert len(fups) <= 3


def test_load_open_followups_empty_vault_returns_empty(vault):
    assert load_open_followups(vault) == []


def test_write_without_ticker_still_works(vault):
    p = write_followup(
        vault,
        title="General market question",
        body="Context",
        origin_task_type="dive_macro",
    )
    assert p is not None
    text = p.read_text()
    assert "ticker:" not in text or "ticker: \n" in text or "ticker: null" in text
