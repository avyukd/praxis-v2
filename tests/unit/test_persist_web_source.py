"""Tests for praxis_core.vault.sources.persist_web_source."""

from __future__ import annotations

from praxis_core.vault.sources import persist_web_source


def test_persist_writes_markdown_with_frontmatter(tmp_path):
    p = persist_web_source(
        tmp_path,
        url="https://example.com/article",
        title="Hormuz Shipping Update",
        body_text="Body text about the strait.",
        site="example.com",
    )
    assert p is not None
    text = p.read_text()
    assert text.startswith("---\n")
    assert "url: https://example.com/article" in text
    assert "site: example.com" in text
    assert "url_hash:" in text
    assert "Body text about the strait." in text


def test_persist_dedup_on_repeated_url(tmp_path):
    first = persist_web_source(
        tmp_path,
        url="https://example.com/a",
        title="A",
        body_text="a",
    )
    second = persist_web_source(
        tmp_path,
        url="https://example.com/a",
        title="A (again)",
        body_text="a (again)",
    )
    assert first is not None
    assert second is None  # deduped


def test_persist_infers_site_from_url(tmp_path):
    p = persist_web_source(
        tmp_path,
        url="https://www.nytimes.com/2026/04/21/hormuz.html",
        title="Hormuz Update",
        body_text="body",
    )
    text = p.read_text()
    assert "site: nytimes.com" in text  # stripped www.


def test_persist_rejects_empty_inputs(tmp_path):
    assert persist_web_source(tmp_path, url="", title="x", body_text="y") is None
    assert persist_web_source(tmp_path, url="https://x.com", title="x", body_text="") is None


def test_persist_writes_under_todays_date_folder(tmp_path):
    from praxis_core.time_et import now_et

    p = persist_web_source(
        tmp_path,
        url="https://example.com/today",
        title="Today",
        body_text="today body",
    )
    expected_folder = tmp_path / "_raw" / "manual" / now_et().strftime("%Y-%m-%d")
    assert p.parent == expected_folder


def test_persist_includes_related_nodes(tmp_path):
    p = persist_web_source(
        tmp_path,
        url="https://example.com/x",
        title="X",
        body_text="body",
        related_nodes=["themes/hormuz", "questions/hormuz-fertilizer"],
    )
    text = p.read_text()
    assert "themes/hormuz" in text
    assert "questions/hormuz-fertilizer" in text


def test_persist_truncates_body(tmp_path):
    long_body = "Z" * 30000  # unique marker avoids collisions with rest of file
    p = persist_web_source(
        tmp_path,
        url="https://example.com/long",
        title="Long",
        body_text=long_body,
        max_body_chars=100,
    )
    text = p.read_text()
    assert text.count("Z") == 100  # exactly max_body_chars, no more
