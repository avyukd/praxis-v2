from __future__ import annotations

import json

from praxis_core.llm.stream_parser import StreamParser, resets_at_to_wait_seconds


def _feed_events(parser: StreamParser, events: list[dict]) -> None:
    for e in events:
        parser.feed_line(json.dumps(e))


def test_parses_real_happy_path() -> None:
    """Use actual events captured from claude -p 2.1.114."""
    parser = StreamParser()
    _feed_events(
        parser,
        [
            {"type": "system", "subtype": "init", "session_id": "s1"},
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "allowed",
                    "resetsAt": 1776585600,
                    "rateLimitType": "five_hour",
                    "overageStatus": "rejected",
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "thinking", "thinking": "user wants ok"}],
                    "usage": {"input_tokens": 10, "output_tokens": 7},
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "ok"}],
                    "usage": {"input_tokens": 10, "output_tokens": 7},
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "ok",
                "stop_reason": "end_turn",
                "total_cost_usd": 0.041,
                "usage": {"input_tokens": 10, "output_tokens": 50},
            },
        ],
    )
    assert parser.saw_result
    assert parser.final_text == "ok"
    assert parser.tokens_in == 10
    assert parser.tokens_out == 50
    assert parser.cost_usd == 0.041
    assert not parser.rate_limit_hit
    assert parser.rate_limit_resets_at == 1776585600


def test_allowed_rate_limit_event_not_flagged() -> None:
    """rate_limit_event with status=allowed is just informational, not a hit."""
    parser = StreamParser()
    _feed_events(
        parser,
        [
            {
                "type": "rate_limit_event",
                "rate_limit_info": {"status": "allowed", "resetsAt": 1776585600},
            },
        ],
    )
    assert not parser.rate_limit_hit
    assert parser.rate_limit_resets_at == 1776585600


def test_rejected_rate_limit_event_flagged() -> None:
    parser = StreamParser()
    _feed_events(
        parser,
        [
            {
                "type": "rate_limit_event",
                "rate_limit_info": {"status": "rejected", "resetsAt": 1776589000},
            },
        ],
    )
    assert parser.rate_limit_hit


def test_tool_use_message_is_not_rate_limit() -> None:
    """A `tool_use` assistant content block shouldn't trigger rate-limit detection."""
    parser = StreamParser()
    _feed_events(
        parser,
        [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Write",
                            "input": {"file_path": "/x", "content": "y"},
                        }
                    ]
                },
            }
        ],
    )
    assert not parser.rate_limit_hit


def test_detects_rate_limit_in_result() -> None:
    parser = StreamParser()
    _feed_events(
        parser,
        [
            {
                "type": "result",
                "subtype": "error",
                "is_error": True,
                "result": "Rate limit exceeded, please wait until 3pm",
            }
        ],
    )
    assert parser.rate_limit_hit
    assert parser.hit_error


def test_detects_rate_limit_in_error_event() -> None:
    parser = StreamParser()
    _feed_events(parser, [{"type": "error", "message": "rate_limit_error: please wait"}])
    assert parser.rate_limit_hit


def test_detects_rate_limit_in_assistant_text() -> None:
    parser = StreamParser()
    _feed_events(
        parser,
        [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Hit usage limit"}]},
            }
        ],
    )
    assert parser.rate_limit_hit


def test_ignores_malformed_json() -> None:
    parser = StreamParser()
    parser.feed_line("not json")
    parser.feed_line("")
    parser.feed_line("  ")
    assert not parser.events


def test_ignores_non_dict_events() -> None:
    parser = StreamParser()
    parser.feed_line('["array", "not", "dict"]')
    parser.feed_line('"just a string"')
    assert not parser.events


def test_missing_usage_fields_not_fatal() -> None:
    parser = StreamParser()
    _feed_events(
        parser,
        [{"type": "result", "subtype": "success", "result": "done"}],
    )
    assert parser.saw_result
    assert parser.tokens_in is None


def test_non_numeric_cost_ignored() -> None:
    parser = StreamParser()
    _feed_events(
        parser,
        [
            {
                "type": "result",
                "subtype": "success",
                "result": "x",
                "total_cost_usd": "not_a_number",
            }
        ],
    )
    assert parser.cost_usd is None


def test_resets_at_to_wait_seconds_future() -> None:
    import time

    future = int(time.time()) + 600
    wait = resets_at_to_wait_seconds(future)
    assert wait is not None
    assert 590 <= wait <= 610


def test_resets_at_to_wait_seconds_past() -> None:
    import time

    past = int(time.time()) - 100
    assert resets_at_to_wait_seconds(past) == 0


def test_resets_at_none() -> None:
    assert resets_at_to_wait_seconds(None) is None
