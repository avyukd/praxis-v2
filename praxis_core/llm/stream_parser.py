from __future__ import annotations

import json
from dataclasses import dataclass, field
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

    The format emits one JSON object per line. The key events we care about:
      - `{"type": "system", "subtype": "init", ...}`             -> session init
      - `{"type": "assistant", "message": {...}}`                 -> each turn
      - `{"type": "user", "message": {"content": [...]}}`         -> tool results
      - `{"type": "result", "subtype": "...", "result": "...",    -> terminal
            "usage": {...}, "total_cost_usd": ...}`

    We treat a rate limit as:
      - `subtype == "error"` and the payload text mentions a rate limit marker, OR
      - a result event with non-success subtype and rate-limit-marker text

    We're deliberately tolerant — the stream format is not frozen and we don't
    want to wedge on schema churn.
    """

    events: list[ClaudeStreamEvent] = field(default_factory=list)
    final_text: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    rate_limit_hit: bool = False
    hit_error: bool = False
    saw_result: bool = False

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

        if t == "result":
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
            subtype = str(obj.get("subtype", ""))
            is_err = subtype not in {"", "success"} and subtype.startswith(
                ("error", "rate", "failure")
            )
            if is_err:
                event.is_error = True
                self.hit_error = True
            if isinstance(result_text, str) and _text_has_rate_limit(result_text):
                event.is_rate_limit = True
                self.rate_limit_hit = True
            if subtype.startswith("rate") or (subtype.startswith("error") and is_err):
                if _text_has_rate_limit(str(result_text or "") + " " + subtype):
                    event.is_rate_limit = True
                    self.rate_limit_hit = True
        elif t == "error":
            event.is_error = True
            self.hit_error = True
            message = str(obj.get("message", ""))
            if _text_has_rate_limit(message):
                event.is_rate_limit = True
                self.rate_limit_hit = True
        elif t == "assistant":
            msg = obj.get("message") or {}
            if isinstance(msg, dict):
                content = msg.get("content") or []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text" and _text_has_rate_limit(
                            str(block.get("text", ""))
                        ):
                            event.is_rate_limit = True
                            self.rate_limit_hit = True

        self.events.append(event)
        return event


def _safe_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
