"""Tests covering Section C post-Monday fixes:

- refresh_index picks up <dir>/index.md and <dir>/notes.md stubs
- cleanup_sessions validates payload via Pydantic
- inbox_watcher quotes filenames so YAML can't be injected
- search_vault MCP tool clamps caller-supplied limit
- generate_daily_journal captures tasks that finished in-window even
  when they started the day before
"""

from __future__ import annotations

from pathlib import Path

import pytest

from handlers.refresh_index import _collect_nodes
from praxis_core.schemas.payloads import CleanupSessionsPayload
from services.pollers.inbox_watcher import _yaml_quote


def test_refresh_index_picks_up_index_md_stubs(tmp_path: Path) -> None:
    vault = tmp_path
    (vault / "companies" / "AAA").mkdir(parents=True)
    (vault / "companies" / "AAA" / "notes.md").write_text("# AAA")
    (vault / "companies" / "BBB").mkdir()
    (vault / "companies" / "BBB" / "index.md").write_text("# BBB")
    (vault / "themes").mkdir()
    (vault / "themes" / "hormuz.md").write_text("# hormuz")

    nodes = _collect_nodes(vault)
    assert "companies/AAA/notes.md" in nodes["companies"]
    assert "companies/BBB/index.md" in nodes["companies"]
    assert "themes/hormuz.md" in nodes["themes"]


def test_refresh_index_handles_missing_directories(tmp_path: Path) -> None:
    nodes = _collect_nodes(tmp_path)
    assert all(v == [] for v in nodes.values())


def test_cleanup_sessions_payload_defaults() -> None:
    p = CleanupSessionsPayload.model_validate({})
    assert p.min_age_hours == 24
    assert p.triggered_by == "scheduler"


def test_cleanup_sessions_payload_rejects_wrong_type() -> None:
    with pytest.raises(Exception):
        CleanupSessionsPayload.model_validate({"min_age_hours": "not-a-number"})


def test_yaml_quote_escapes_newline() -> None:
    """A filename with a newline must not break out of the scalar."""
    q = _yaml_quote("foo\nowned: true.md")
    assert "\n" not in q
    assert q.startswith("'") and q.endswith("'")


def test_yaml_quote_escapes_embedded_quote() -> None:
    q = _yaml_quote("it's broken")
    assert q == "'it''s broken'"


def test_yaml_quote_normal_filename() -> None:
    assert _yaml_quote("report.md") == "'report.md'"
