from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class ClaudeStreamEvent:
    event_type: str
    raw: dict[str, Any]
    is_rate_limit: bool = False
    is_error: bool = False
    is_result: bool = False
    final_text: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    # If this is a rate-limit event, the unix timestamp when limits reset (None if unknown)
    rate_limit_resets_at: int | None = None


RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "rate_limit",
    "rate-limit",
    "rate limit",
    "usage limit",
    "quota",
    "please wait",
)


def _text_has_rate_limit(text: str) -> bool:
    lower = text.lower()
    return any(m in lower for m in RATE_LIMIT_MARKERS)


@dataclass
class StreamParser:
    """Parses Claude Code `--output-format=stream-json --verbose` event stream.

    Event types observed in Claude Code 2.1.114:
      - `{"type": "system", "subtype": "init", "session_id", "tools": [...], "mcp_servers": [...], "model": "..."}`
      - `{"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"|"rejected",
             "resetsAt": <unix_ts>, "rateLimitType": "five_hour", "overageStatus": "rejected"|...}}`
      - `{"type": "assistant", "message": {"content": [{"type": "text"|"thinking"|"tool_use", ...}], "usage": {...}}}`
      - `{"type": "user", "message": {"content": [{"type": "tool_result", ...}]}}`
      - `{"type": "result", "subtype": "success"|"error", "is_error": bool, "result": str, "stop_reason": "end_turn"|"max_tokens"|...,
             "total_cost_usd": float, "usage": {"input_tokens", "output_tokens", ...}, "num_turns": int, "permission_denials": []}`
    """

    events: list[ClaudeStreamEvent] = field(default_factory=list)
    final_text: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    rate_limit_hit: bool = False
    hit_error: bool = False
    saw_result: bool = False
    rate_limit_resets_at: int | None = None

    def feed_line(self, line: str) -> ClaudeStreamEvent | None:
        line = line.strip()
        if not line:
            return None
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        return self._handle_event(obj)

    def _handle_event(self, obj: dict[str, Any]) -> ClaudeStreamEvent:
        t = str(obj.get("type", "unknown"))
        event = ClaudeStreamEvent(event_type=t, raw=obj)

        if t == "rate_limit_event":
            self._handle_rate_limit_event(event, obj)
        elif t == "result":
            self._handle_result_event(event, obj)
        elif t == "error":
            self._handle_error_event(event, obj)
        elif t == "assistant":
            self._handle_assistant_event(event, obj)

        self.events.append(event)
        return event

    def _handle_rate_limit_event(self, event: ClaudeStreamEvent, obj: dict[str, Any]) -> None:
        """Claude Code emits this every invocation; structured rate-limit status.

        Only treat it as a 'rate limit hit' if status is not 'allowed'.
        """
        info = obj.get("rate_limit_info") or {}
        if not isinstance(info, dict):
            return
        resets_at = info.get("resetsAt")
        if isinstance(resets_at, (int, float)):
            event.rate_limit_resets_at = int(resets_at)
            self.rate_limit_resets_at = int(resets_at)
        status = str(info.get("status", "")).lower()
        if status and status not in {"allowed", "ok", ""}:
            event.is_rate_limit = True
            self.rate_limit_hit = True

    def _handle_result_event(self, event: ClaudeStreamEvent, obj: dict[str, Any]) -> None:
        event.is_result = True
        self.saw_result = True

        result_text = obj.get("result")
        if isinstance(result_text, str):
            event.final_text = result_text
            self.final_text = result_text

        usage = obj.get("usage") or {}
        if isinstance(usage, dict):
            event.tokens_in = _safe_int(usage.get("input_tokens"))
            event.tokens_out = _safe_int(usage.get("output_tokens"))
            self.tokens_in = event.tokens_in or self.tokens_in
            self.tokens_out = event.tokens_out or self.tokens_out

        cost = obj.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            event.cost_usd = float(cost)
            self.cost_usd = event.cost_usd

        is_error = bool(obj.get("is_error")) or str(obj.get("subtype", "")) == "error"
        if is_error:
            event.is_error = True
            self.hit_error = True

        # Check for rate-limit markers in the result text or subtype
        subtype = str(obj.get("subtype", ""))
        combined = f"{result_text or ''} {subtype}"
        if _text_has_rate_limit(combined):
            event.is_rate_limit = True
            self.rate_limit_hit = True

    def _handle_error_event(self, event: ClaudeStreamEvent, obj: dict[str, Any]) -> None:
        event.is_error = True
        self.hit_error = True
        message = str(obj.get("message", ""))
        if _text_has_rate_limit(message):
            event.is_rate_limit = True
            self.rate_limit_hit = True

    def _handle_assistant_event(self, event: ClaudeStreamEvent, obj: dict[str, Any]) -> None:
        """Assistant messages may contain `thinking`, `text`, or `tool_use` blocks.
        Capture per-turn token usage if present. Rate-limit detection from text blocks."""
        msg = obj.get("message") or {}
        if not isinstance(msg, dict):
            return

        # Pull per-turn usage for telemetry (not strictly required; result event has totals)
        usage = msg.get("usage") or {}
        if isinstance(usage, dict):
            ti = _safe_int(usage.get("input_tokens"))
            to = _safe_int(usage.get("output_tokens"))
            if ti is not None:
                self.tokens_in = (self.tokens_in or 0) + ti if self.tokens_in else ti
            if to is not None:
                self.tokens_out = (self.tokens_out or 0) + to if self.tokens_out else to

        content = msg.get("content") or []
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and _text_has_rate_limit(str(block.get("text", ""))):
                event.is_rate_limit = True
                self.rate_limit_hit = True


def _safe_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def resets_at_to_wait_seconds(resets_at: int | None) -> int | None:
    """Given a unix timestamp for when rate limits reset, return seconds to wait."""
    if resets_at is None:
        return None
    now = datetime.now(UTC).timestamp()
    wait = int(resets_at - now)
    return max(0, wait)
