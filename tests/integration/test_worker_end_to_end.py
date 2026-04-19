from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from praxis_core.config import get_settings
from praxis_core.llm.invoker import LLMResult
from praxis_core.schemas.task_types import TaskType
from praxis_core.tasks.enqueue import enqueue_task
from praxis_core.tasks.lifecycle import claim_next_task
from praxis_core.vault import conventions as vc

from handlers import HandlerContext, HandlerResult


def _make_triage_artifacts(vault_root: Path, accession: str) -> None:
    d = vc.analyzed_filing_dir(vault_root, "8-K", accession)
    d.mkdir(parents=True, exist_ok=True)
    (d / "triage.md").write_text("triage rationale\n\n[[raw]]")
    (d / "triage.json").write_text(
        json.dumps(
            {
                "accession": accession,
                "form_type": "8-K",
                "ticker": "NVDA",
                "score": 4,
                "category": "guidance",
                "one_sentence_why": "guidance raise",
                "warrants_deep_read": True,
            }
        )
    )


@pytest.mark.asyncio
async def test_refresh_index_handler_real_write(vault_root: Path) -> None:
    from handlers.refresh_index import handle

    (vault_root / "companies" / "NVDA").mkdir(parents=True, exist_ok=True)
    (vault_root / "companies" / "NVDA" / "notes.md").write_text("# NVDA")
    (vault_root / "themes").mkdir(parents=True, exist_ok=True)
    (vault_root / "themes" / "ai-capex.md").write_text("# AI capex")

    ctx = HandlerContext(
        task_id="test",
        task_type="refresh_index",
        payload={"scope": "full", "triggered_by": "test"},
        vault_root=vault_root,
        model="haiku",
    )
    result = await handle(ctx)
    assert result.ok
    index = vault_root / "INDEX.md"
    assert index.exists()
    content = index.read_text()
    assert "NVDA" in content or "notes" in content
    assert "ai-capex" in content


@pytest.mark.asyncio
async def test_lint_vault_handler_generates_report(vault_root: Path) -> None:
    from handlers.lint_vault import handle

    (vault_root / "companies" / "NVDA").mkdir(parents=True, exist_ok=True)
    (vault_root / "companies" / "NVDA" / "notes.md").write_text(
        "---\ntype: company_note\nticker: NVDA\n---\n\n# NVDA\n\nLink to [[nonexistent-target]]"
    )

    ctx = HandlerContext(
        task_id="test",
        task_type="lint_vault",
        payload={"triggered_by": "test"},
        vault_root=vault_root,
        model="sonnet",
    )
    result = await handle(ctx)
    assert result.ok
    # The lint report should exist
    reports = list((vault_root / "journal").glob("*-lint.md"))
    assert len(reports) == 1
    report_text = reports[0].read_text()
    assert "broken_wikilink" in report_text


@pytest.mark.asyncio
async def test_generate_daily_journal_empty(vault_root: Path, db_session) -> None:
    from handlers.generate_daily_journal import handle
    from praxis_core.db.session import reset_sessionmaker

    # Repoint sessionmaker at test DB
    import os

    os.environ["DATABASE_URL"] = os.environ["PRAXIS_TEST_DATABASE_URL"].replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    from praxis_core.config import get_settings

    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None
    reset_sessionmaker()

    ctx = HandlerContext(
        task_id="test",
        task_type="generate_daily_journal",
        payload={"date": "2026-04-18", "triggered_by": "test"},
        vault_root=vault_root,
        model="haiku",
    )
    result = await handle(ctx)
    assert result.ok
    out = vault_root / "journal" / "2026-04-18.md"
    assert out.exists()
    assert "Daily journal" in out.read_text()
