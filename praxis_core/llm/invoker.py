from __future__ import annotations

import asyncio
import os
import shutil
import signal
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from praxis_core.config import get_settings
from praxis_core.llm.stream_parser import StreamParser
from praxis_core.logging import get_logger
from praxis_core.schemas.task_types import (
    MODEL_TO_API_NAME,
    MODEL_TO_CLI_FLAG,
    TaskModel,
)

log = get_logger("llm.invoker")


FinishReason = Literal["stop", "max_turns", "rate_limit", "timeout", "error", "killed"]


class ToolCall(BaseModel):
    tool_name: str
    input: dict[str, Any]


class LLMResult(BaseModel):
    text: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    duration_s: float
    finish_reason: FinishReason
    raw_events: list[dict[str, Any]] = Field(default_factory=list)
    model: str
    invoker: Literal["cli", "api"]


MODEL_BUDGETS_USD: dict[TaskModel, float] = {
    TaskModel.HAIKU: 0.50,
    TaskModel.SONNET: 2.50,
    TaskModel.OPUS: 6.00,
    TaskModel.NONE: 0.0,
}


def _locate_claude_cli() -> str:
    """Resolve the claude CLI binary path. Systemd services have minimal PATH
    that often excludes ~/.local/bin where Claude Code installs."""
    found = shutil.which("claude")
    if found:
        return found
    candidates = [
        Path.home() / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
        Path("/opt/claude/claude"),
    ]
    for c in candidates:
        if c.exists() and c.is_file():
            return str(c)
    return "claude"  # fall through; will fail loudly at subprocess launch


class LLMInvoker(Protocol):
    invoker_kind: Literal["cli", "api"]

    async def run(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: TaskModel,
        max_budget_usd: float | None = None,
        timeout_s: int | None = None,
        no_event_timeout_s: int | None = None,
        mcp_config_path: str | None = None,
        allowed_tools: list[str] | None = None,
        session_dir: Path | None = None,
    ) -> LLMResult: ...


@dataclass
class CLIInvoker:
    invoker_kind: Literal["cli", "api"] = "cli"

    async def run(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: TaskModel,
        max_budget_usd: float | None = None,
        timeout_s: int | None = None,
        no_event_timeout_s: int | None = None,
        mcp_config_path: str | None = None,
        allowed_tools: list[str] | None = None,
        session_dir: Path | None = None,
    ) -> LLMResult:
        settings = get_settings()
        timeout_s = timeout_s or settings.cli_wall_clock_timeout_s
        no_event_timeout_s = no_event_timeout_s or settings.cli_no_event_timeout_s
        budget = max_budget_usd if max_budget_usd is not None else MODEL_BUDGETS_USD[model]

        if model is TaskModel.NONE:
            raise ValueError("CLIInvoker.run called with model=NONE")

        if session_dir is None:
            session_dir = settings.claude_sessions_root / f"session-{uuid.uuid4().hex}"
        session_dir.mkdir(parents=True, exist_ok=True)

        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("CLAUDE_API_KEY", None)

        cmd = [
            _locate_claude_cli(),
            "-p",
            user_prompt,
            "--output-format=stream-json",
            "--verbose",
            f"--model={MODEL_TO_CLI_FLAG[model]}",
            "--dangerously-skip-permissions",
            f"--max-budget-usd={budget}",
        ]
        if system_prompt:
            cmd.extend(["--append-system-prompt", system_prompt])
        if mcp_config_path:
            cmd.extend(["--mcp-config", mcp_config_path])
        if allowed_tools:
            # Claude CLI takes either comma-separated or space-separated; use a single arg
            cmd.extend(["--allowedTools", ",".join(allowed_tools)])

        log.info(
            "cli.invoke.start",
            model=str(model),
            cwd=str(session_dir),
            budget_usd=budget,
            timeout_s=timeout_s,
        )

        started_at = time.monotonic()
        parser = StreamParser()

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(session_dir),
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # so we can killpg the whole tree
        )

        finish: FinishReason = "error"

        async def _read_stdout() -> FinishReason:
            assert proc.stdout is not None
            nonlocal finish
            while True:
                try:
                    line_bytes = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=no_event_timeout_s
                    )
                except TimeoutError:
                    log.warning(
                        "cli.invoke.no_event_timeout",
                        gap_s=no_event_timeout_s,
                    )
                    return "timeout"
                if not line_bytes:
                    return finish
                try:
                    line = line_bytes.decode("utf-8", errors="replace")
                except Exception:
                    continue
                event = parser.feed_line(line)
                if event is None:
                    continue
                if event.is_rate_limit:
                    log.warning(
                        "cli.invoke.rate_limit_detected",
                        event_type=event.event_type,
                    )
                    return "rate_limit"
                if event.is_result:
                    return "stop" if not parser.hit_error else "error"

        async def _read_with_soft_warning() -> FinishReason:
            """Wraps _read_stdout to emit a journal-visible warning 5 min
            before the hard wall timeout fires, so long-running dives are
            flagged (but not killed) for observability."""
            soft_warn_after = max(60, timeout_s - 300)
            reader = asyncio.create_task(_read_stdout())
            try:
                done, _ = await asyncio.wait({reader}, timeout=soft_warn_after)
                if reader in done:
                    return reader.result()
                log.warning(
                    "cli.invoke.long_running",
                    elapsed_s=int(time.monotonic() - started_at),
                    hard_timeout_s=timeout_s,
                    grace_remaining_s=timeout_s - soft_warn_after,
                )
                return await reader
            except Exception:
                reader.cancel()
                raise

        try:
            try:
                finish = await asyncio.wait_for(
                    _read_with_soft_warning(), timeout=timeout_s
                )
            except TimeoutError:
                log.warning(
                    "cli.invoke.wall_timeout",
                    duration_s=time.monotonic() - started_at,
                    timeout_s=timeout_s,
                    action="sending SIGTERM — CLI has grace window to flush "
                    "output before SIGKILL",
                )
                finish = "timeout"

            # Ensure process is reaped. If we're terminating early, kill the
            # group — _kill_proc_tree sends SIGTERM first with a 60s grace
            # window so Claude CLI can flush partial output and exit clean.
            if proc.returncode is None:
                await self._kill_proc_tree(proc)

            try:
                returncode = await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                returncode = -1

            if finish == "error" and returncode != 0 and not parser.saw_result:
                finish = "rate_limit" if parser.rate_limit_hit else "error"

            duration = time.monotonic() - started_at
            log.info(
                "cli.invoke.done",
                finish=finish,
                duration_s=duration,
                returncode=returncode,
                tokens_in=parser.tokens_in,
                tokens_out=parser.tokens_out,
                cost_usd=parser.cost_usd,
            )

            return LLMResult(
                text=parser.final_text or "",
                tokens_in=parser.tokens_in,
                tokens_out=parser.tokens_out,
                cost_usd=parser.cost_usd,
                duration_s=duration,
                finish_reason=finish,
                raw_events=[e.raw for e in parser.events],
                model=str(model),
                invoker="cli",
            )
        finally:
            if proc.returncode is None:
                await self._kill_proc_tree(proc)
            # Best-effort cleanup of session_dir; deferred cleanup job handles older ones too
            try:
                if session_dir.exists() and session_dir.parent == settings.claude_sessions_root:
                    shutil.rmtree(session_dir, ignore_errors=True)
            except Exception as e:
                log.debug("cli.invoke.session_cleanup_fail", error=str(e))

    @staticmethod
    async def _kill_proc_tree(proc: asyncio.subprocess.Process) -> None:
        """Kill the process group so child processes (MCP servers, tool
        subprocs) die too. SIGTERM → 60s grace for Claude CLI to flush any
        in-progress tool output and exit gracefully → SIGKILL if still
        alive. The 60s grace window is deliberately long so a Write-tool
        call that was mid-flight at SIGTERM time has a full minute to
        complete and preserve the dive artifact on disk."""
        if proc.returncode is not None:
            return
        try:
            pid = proc.pid
            try:
                os.killpg(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.terminate()
            log.info("cli.invoke.sigterm_sent", pid=pid, grace_s=60)
            try:
                await asyncio.wait_for(proc.wait(), timeout=60)
                log.info("cli.invoke.sigterm_clean_exit", pid=pid)
                return
            except TimeoutError:
                log.warning("cli.invoke.sigterm_no_exit", pid=pid, action="sigkill")
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                pass
        except Exception as e:
            log.warning("cli.invoke.kill_fail", error=str(e))


@dataclass
class APIInvoker:
    invoker_kind: Literal["cli", "api"] = "api"

    async def run(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: TaskModel,
        max_budget_usd: float | None = None,
        timeout_s: int | None = None,
        no_event_timeout_s: int | None = None,
        mcp_config_path: str | None = None,
        allowed_tools: list[str] | None = None,
        session_dir: Path | None = None,
    ) -> LLMResult:
        from anthropic import AsyncAnthropic

        _ = max_budget_usd  # API mode doesn't use CLI-level budget cap; rely on timeout

        settings = get_settings()
        timeout_s = timeout_s or settings.cli_wall_clock_timeout_s

        if model is TaskModel.NONE:
            raise ValueError("APIInvoker.run called with model=NONE")

        api_model = MODEL_TO_API_NAME[model]
        log.info("api.invoke.start", model=api_model, timeout_s=timeout_s)

        client = AsyncAnthropic(timeout=timeout_s)
        started_at = time.monotonic()

        try:
            response = await client.messages.create(
                model=api_model,
                max_tokens=8192,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            duration = time.monotonic() - started_at

            text = ""
            for block in response.content:
                if getattr(block, "type", None) == "text":
                    text += getattr(block, "text", "")

            finish: FinishReason = (
                "stop"
                if response.stop_reason == "end_turn"
                else "max_turns"
                if response.stop_reason == "max_tokens"
                else "error"
            )

            return LLMResult(
                text=text,
                tokens_in=response.usage.input_tokens,
                tokens_out=response.usage.output_tokens,
                cost_usd=None,
                duration_s=duration,
                finish_reason=finish,
                raw_events=[],
                model=str(model),
                invoker="api",
            )
        except Exception as e:
            duration = time.monotonic() - started_at
            msg = str(e).lower()
            is_rate_limit = "rate_limit" in msg or "429" in msg or "quota" in msg
            log.warning(
                "api.invoke.error",
                error=str(e),
                duration_s=duration,
                is_rate_limit=is_rate_limit,
            )
            return LLMResult(
                text="",
                duration_s=duration,
                finish_reason="rate_limit" if is_rate_limit else "error",
                model=str(model),
                invoker="api",
            )


_invoker: CLIInvoker | APIInvoker | None = None


def get_invoker() -> CLIInvoker | APIInvoker:
    global _invoker
    if _invoker is None:
        settings = get_settings()
        _invoker = APIInvoker() if settings.praxis_invoker == "api" else CLIInvoker()
    assert _invoker is not None
    return _invoker


def reset_invoker() -> None:
    global _invoker
    _invoker = None
