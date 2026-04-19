"""Live test: actually runs `claude -p` against the user's Max subscription.

Skipped unless PRAXIS_TEST_REAL_CLAUDE=1 is set — it burns Max quota (a few cents per run).
Run manually to validate the invoker end-to-end:

    PRAXIS_TEST_REAL_CLAUDE=1 .venv/bin/python -m pytest tests/integration/test_cli_invoker_real.py -v
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest

from praxis_core.llm.invoker import CLIInvoker
from praxis_core.schemas.task_types import TaskModel


def _should_run() -> bool:
    return os.environ.get("PRAXIS_TEST_REAL_CLAUDE") == "1" and shutil.which("claude") is not None


@pytest.mark.asyncio
async def test_real_cli_minimal_haiku(tmp_path: Path) -> None:
    if not _should_run():
        pytest.skip("PRAXIS_TEST_REAL_CLAUDE=1 required + claude on PATH")

    invoker = CLIInvoker()
    session_dir = tmp_path / f"session-{uuid.uuid4().hex}"
    session_dir.mkdir()
    result = await invoker.run(
        system_prompt="You are a probe. Reply with exactly 'ok' and nothing else.",
        user_prompt="probe",
        model=TaskModel.HAIKU,
        max_budget_usd=0.10,
        timeout_s=60,
        no_event_timeout_s=30,
        session_dir=session_dir,
    )
    assert result.finish_reason == "stop"
    assert "ok" in result.text.lower()
    assert result.tokens_in is not None and result.tokens_in > 0
    assert result.tokens_out is not None and result.tokens_out > 0
    assert result.cost_usd is not None and result.cost_usd > 0


@pytest.mark.asyncio
async def test_real_cli_tool_use_write(tmp_path: Path) -> None:
    """Verify Claude can use Write tool through our invoker."""
    if not _should_run():
        pytest.skip("PRAXIS_TEST_REAL_CLAUDE=1 required + claude on PATH")

    invoker = CLIInvoker()
    session_dir = tmp_path / f"session-{uuid.uuid4().hex}"
    session_dir.mkdir()
    output_file = session_dir / "probe-output.txt"

    result = await invoker.run(
        system_prompt="",
        user_prompt=(
            f"Write a file at {output_file} containing exactly: probe-wrote. "
            "Then respond with 'done'."
        ),
        model=TaskModel.HAIKU,
        max_budget_usd=0.20,
        timeout_s=60,
        no_event_timeout_s=30,
        allowed_tools=["Write"],
        session_dir=session_dir,
    )
    assert result.finish_reason == "stop"
    assert output_file.exists()
    assert "probe-wrote" in output_file.read_text()
