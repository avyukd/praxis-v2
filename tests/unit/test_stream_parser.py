from __future__ import annotations

import json

from praxis_core.llm.stream_parser import StreamParser


def _feed_events(parser: StreamParser, events: list[dict]) -> None:
    for e in events:
        parser.feed_line(json.dumps(e))


def test_parses_happy_path() -> None:
    parser = StreamParser()
    _feed_events(
        parser,
        [
            {"type": "system", "subtype": "init", "session_id": "abc"},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Thinking..."}]}},
            {
                "type": "result",
                "subtype": "success",
                "result": "Final answer here",
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "total_cost_usd": 0.01,
            },
        ],
    )
    assert parser.saw_result
    assert parser.final_text == "Final answer here"
    assert parser.tokens_in == 100
    assert parser.tokens_out == 50
    assert parser.cost_usd == 0.01
    assert not parser.rate_limit_hit


def test_detects_rate_limit_in_result() -> None:
    parser = StreamParser()
    _feed_events(
        parser,
        [
            {
                "type": "result",
                "subtype": "error",
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
        [{"type": "result", "subtype": "success", "result": "x", "total_cost_usd": "not_a_number"}],
    )
    assert parser.cost_usd is None
