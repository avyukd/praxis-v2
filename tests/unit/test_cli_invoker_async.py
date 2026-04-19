from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from praxis_core.llm.invoker import CLIInvoker, LLMResult
from praxis_core.schemas.task_types import TaskModel


def _write_fake_claude(tmp_path: Path, events: list[dict], exit_code: int = 0) -> Path:
    """Write a fake 'claude' binary that emits stream-json lines then exits.

    The fake ignores all flags — we only care about proving the invoker parses
    stream output correctly and manages the subprocess lifecycle.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    claude = bin_dir / "claude"
    events_json = "\n".join(json.dumps(e) for e in events)
    script = f"""#!/usr/bin/env bash
cat <<'PRAXIS_EOF'
{events_json}
PRAXIS_EOF
exit {exit_code}
"""
    claude.write_text(script)
    claude.chmod(claude.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def _write_slow_claude(tmp_path: Path, sleep_seconds: int) -> Path:
    """Fake claude that sleeps a long time without emitting events — tests timeouts."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    claude = bin_dir / "claude"
    script = f"""#!/usr/bin/env bash
sleep {sleep_seconds}
"""
    claude.write_text(script)
    claude.chmod(claude.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def _run_with_path(path_dir: Path, coro):
    async def _wrapped():
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{path_dir}:{old}"
        try:
            return await coro
        finally:
            os.environ["PATH"] = old

    return asyncio.run(_wrapped())


@pytest.mark.asyncio
async def test_cli_invoker_parses_result_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = _write_fake_claude(
        tmp_path,
        [
            {"type": "system", "subtype": "init", "session_id": "x"},
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "thinking"}]},
            },
            {
                "type": "result",
                "subtype": "success",
                "result": "all done",
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "total_cost_usd": 0.005,
            },
        ],
    )
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    invoker = CLIInvoker()
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    result = await invoker.run(
        system_prompt="test",
        user_prompt="hello",
        model=TaskModel.HAIKU,
        max_budget_usd=0.10,
        timeout_s=30,
        no_event_timeout_s=10,
        session_dir=session_dir,
    )
    assert isinstance(result, LLMResult)
    assert result.finish_reason == "stop"
    assert result.text == "all done"
    assert result.tokens_in == 100
    assert result.tokens_out == 50
    assert result.cost_usd == 0.005


@pytest.mark.asyncio
async def test_cli_invoker_detects_rate_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = _write_fake_claude(
        tmp_path,
        [
            {
                "type": "result",
                "subtype": "error",
                "result": "Rate limit exceeded, please wait until 3pm",
            }
        ],
    )
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    invoker = CLIInvoker()
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    result = await invoker.run(
        system_prompt="",
        user_prompt="hi",
        model=TaskModel.HAIKU,
        max_budget_usd=0.10,
        timeout_s=30,
        no_event_timeout_s=10,
        session_dir=session_dir,
    )
    assert result.finish_reason == "rate_limit"


@pytest.mark.asyncio
async def test_cli_invoker_no_event_timeout_kills_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = _write_slow_claude(tmp_path, sleep_seconds=30)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    invoker = CLIInvoker()
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    result = await invoker.run(
        system_prompt="",
        user_prompt="hi",
        model=TaskModel.HAIKU,
        max_budget_usd=0.10,
        timeout_s=30,
        no_event_timeout_s=2,
        session_dir=session_dir,
    )
    assert result.finish_reason == "timeout"
    # Process should have been killed — total duration should be ~2-3s, not 30
    assert result.duration_s < 10


@pytest.mark.asyncio
async def test_cli_invoker_strips_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify ANTHROPIC_API_KEY is stripped from subprocess env."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    # Fake claude that emits whether ANTHROPIC_API_KEY is set
    claude.write_text(
        """#!/usr/bin/env bash
if [[ -n "$ANTHROPIC_API_KEY" ]]; then
  echo '{"type":"result","subtype":"error","result":"API_KEY_LEAKED"}'
else
  echo '{"type":"result","subtype":"success","result":"env_clean"}'
fi
exit 0
"""
    )
    claude.chmod(claude.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-dangerous-key-that-should-not-leak")

    invoker = CLIInvoker()
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    result = await invoker.run(
        system_prompt="",
        user_prompt="hi",
        model=TaskModel.HAIKU,
        timeout_s=10,
        no_event_timeout_s=5,
        session_dir=session_dir,
    )
    assert result.finish_reason == "stop"
    assert result.text == "env_clean"


@pytest.mark.asyncio
async def test_cli_invoker_does_not_block_concurrent_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two concurrent CLI invocations should both complete in roughly parallel time —
    proves the async invoker doesn't block the event loop."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    # Fake claude that takes 1s then emits success
    claude.write_text(
        """#!/usr/bin/env bash
sleep 1
echo '{"type":"result","subtype":"success","result":"ok"}'
exit 0
"""
    )
    claude.chmod(claude.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    import time

    invoker = CLIInvoker()

    async def one_call(i: int) -> LLMResult:
        session_dir = tmp_path / f"session-{i}"
        session_dir.mkdir()
        return await invoker.run(
            system_prompt="",
            user_prompt="hi",
            model=TaskModel.HAIKU,
            timeout_s=10,
            no_event_timeout_s=5,
            session_dir=session_dir,
        )

    start = time.monotonic()
    results = await asyncio.gather(one_call(0), one_call(1), one_call(2))
    duration = time.monotonic() - start

    assert all(r.finish_reason == "stop" for r in results)
    # If parallelism works, 3 x 1s calls should finish in well under 3s (with overhead, < 2s)
    assert duration < 2.5, f"concurrent calls serialized: took {duration:.2f}s for 3x1s"
