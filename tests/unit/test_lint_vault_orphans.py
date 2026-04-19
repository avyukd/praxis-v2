from __future__ import annotations

from pathlib import Path

import pytest

from handlers import HandlerContext
from handlers.lint_vault import handle


@pytest.mark.asyncio
async def test_lint_flags_orphan_raw_filing(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "companies").mkdir()
    (vault / "journal").mkdir()

    # Orphan raw filing — has raw, no analyzed
    orphan = vault / "_raw" / "filings" / "8-k" / "0001-26-000001"
    orphan.mkdir(parents=True)
    (orphan / "filing.txt").write_text("orphan 8-K text")

    # Good filing — has both raw and analyzed
    good_raw = vault / "_raw" / "filings" / "8-k" / "0001-26-000002"
    good_raw.mkdir(parents=True)
    (good_raw / "filing.txt").write_text("good 8-K text")
    good_analyzed = vault / "_analyzed" / "filings" / "8-k" / "0001-26-000002"
    good_analyzed.mkdir(parents=True)
    (good_analyzed / "triage.md").write_text("analyzed")

    ctx = HandlerContext(
        task_id="t",
        task_type="lint_vault",
        payload={"triggered_by": "test"},
        vault_root=vault,
        model="sonnet",
    )
    result = await handle(ctx)
    assert result.ok

    reports = list((vault / "journal").glob("*-lint.md"))
    assert len(reports) == 1
    report_text = reports[0].read_text()
    assert "0001-26-000001" in report_text  # orphan flagged
    assert "0001-26-000002" not in report_text  # good one NOT flagged
    assert "raw filing has no _analyzed" in report_text


@pytest.mark.asyncio
async def test_lint_skips_when_no_raw_dir(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    ctx = HandlerContext(
        task_id="t",
        task_type="lint_vault",
        payload={"triggered_by": "test"},
        vault_root=vault,
        model="sonnet",
    )
    result = await handle(ctx)
    assert result.ok
