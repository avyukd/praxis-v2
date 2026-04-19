from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, field
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


class LLMInvoker(Protocol):
    invoker_kind: Literal["cli", "api"]

    def run(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: TaskModel,
        max_turns: int = 15,
        timeout_s: int | None = None,
        no_event_timeout_s: int | None = None,
        mcp_config_path: str | None = None,
        allowed_tools: list[str] | None = None,
        session_dir: Path | None = None,
    ) -> LLMResult: ...


@dataclass
class CLIInvoker:
    invoker_kind: Literal["cli", "api"] = "cli"

    def run(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: TaskModel,
        max_turns: int = 15,
        timeout_s: int | None = None,
        no_event_timeout_s: int | None = None,
        mcp_config_path: str | None = None,
        allowed_tools: list[str] | None = None,
        session_dir: Path | None = None,
    ) -> LLMResult:
        settings = get_settings()
        timeout_s = timeout_s or settings.cli_wall_clock_timeout_s
        no_event_timeout_s = no_event_timeout_s or settings.cli_no_event_timeout_s

        if model is TaskModel.NONE:
            raise ValueError("CLIInvoker.run called with model=NONE")

        if session_dir is None:
            session_dir = settings.claude_sessions_root / f"session-{uuid.uuid4().hex}"
        session_dir.mkdir(parents=True, exist_ok=True)

        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("CLAUDE_API_KEY", None)

        session_id = str(uuid.uuid4())
        cmd = [
            "claude",
            "-p",
            user_prompt,
            "--output-format=stream-json",
            "--verbose",
            f"--model={MODEL_TO_CLI_FLAG[model]}",
            f"--session-id={session_id}",
            f"--max-turns={max_turns}",
        ]
        if system_prompt:
            cmd.extend(["--append-system-prompt", system_prompt])
        if mcp_config_path:
            cmd.extend(["--mcp-config", mcp_config_path])
        if allowed_tools:
            cmd.extend(["--allowedTools", ",".join(allowed_tools)])

        log.info(
            "cli.invoke.start",
            model=model,
            session_id=session_id,
            cwd=str(session_dir),
            max_turns=max_turns,
            timeout_s=timeout_s,
        )

        started_at = time.monotonic()
        parser = StreamParser()
        proc = subprocess.Popen(
            cmd,
            cwd=str(session_dir),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        last_event_at = started_at
        finish: FinishReason = "error"

        try:
            assert proc.stdout is not None
            while True:
                now = time.monotonic()
                if now - started_at > timeout_s:
                    log.warning(
                        "cli.invoke.wall_timeout",
                        duration_s=now - started_at,
                        timeout_s=timeout_s,
                    )
                    self._kill_proc(proc)
                    finish = "timeout"
                    break
                if now - last_event_at > no_event_timeout_s:
                    log.warning(
                        "cli.invoke.no_event_timeout",
                        gap_s=now - last_event_at,
                        timeout_s=no_event_timeout_s,
                    )
                    self._kill_proc(proc)
                    finish = "timeout"
                    break

                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)
                    continue

                last_event_at = time.monotonic()
                event = parser.feed_line(line)
                if event is None:
                    continue
                if event.is_rate_limit:
                    log.warning("cli.invoke.rate_limit_detected", event_type=event.event_type)
                    self._kill_proc(proc)
                    finish = "rate_limit"
                    break
                if event.is_result:
                    finish = "stop" if not parser.hit_error else "error"
                    break

            # Drain remaining output if we broke out early
            if proc.poll() is None:
                self._kill_proc(proc)

            returncode = proc.wait(timeout=5)
            if finish == "error" and returncode != 0 and not parser.saw_result:
                if parser.rate_limit_hit:
                    finish = "rate_limit"
                else:
                    finish = "error"

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
            if proc.poll() is None:
                self._kill_proc(proc)

    @staticmethod
    def _kill_proc(proc: subprocess.Popen[str]) -> None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=10)
                return
            except subprocess.TimeoutExpired:
                pass
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        except ProcessLookupError:
            pass


@dataclass
class APIInvoker:
    invoker_kind: Literal["cli", "api"] = "api"

    def run(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: TaskModel,
        max_turns: int = 15,
        timeout_s: int | None = None,
        no_event_timeout_s: int | None = None,
        mcp_config_path: str | None = None,
        allowed_tools: list[str] | None = None,
        session_dir: Path | None = None,
    ) -> LLMResult:
        from anthropic import Anthropic

        settings = get_settings()
        timeout_s = timeout_s or settings.cli_wall_clock_timeout_s

        if model is TaskModel.NONE:
            raise ValueError("APIInvoker.run called with model=NONE")

        api_model = MODEL_TO_API_NAME[model]
        log.info("api.invoke.start", model=api_model, timeout_s=timeout_s)

        client = Anthropic(timeout=timeout_s)
        started_at = time.monotonic()

        try:
            response = client.messages.create(
                model=api_model,
                max_tokens=8192,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            duration = time.monotonic() - started_at

            text = ""
            for block in response.content:
                if block.type == "text":
                    text += block.text

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


_invoker: LLMInvoker | None = None


def get_invoker() -> LLMInvoker:
    global _invoker
    if _invoker is None:
        settings = get_settings()
        if settings.praxis_invoker == "api":
            _invoker = APIInvoker()
        else:
            _invoker = CLIInvoker()
    return _invoker


def reset_invoker() -> None:
    global _invoker
    _invoker = None
