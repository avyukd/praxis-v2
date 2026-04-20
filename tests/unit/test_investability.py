"""Unit tests for the INVESTABILITY gate (D20)."""

from __future__ import annotations

from services.dispatcher.investability import parse_investability


def test_parse_continue_em_dash():
    text = "...analysis body...\n\nINVESTABILITY: CONTINUE — margins expanding and cash-rich\n"
    decision, reason = parse_investability(text)
    assert decision == "CONTINUE"
    assert "margins expanding" in reason


def test_parse_stop_hyphen():
    text = "body\n\nINVESTABILITY: STOP - going concern doubt, unrestated filings\n"
    decision, reason = parse_investability(text)
    assert decision == "STOP"
    assert "going concern" in reason


def test_parse_case_insensitive():
    text = "body\n\ninvestability: continue — fine\n"
    decision, _ = parse_investability(text)
    assert decision == "CONTINUE"


def test_parse_last_wins():
    text = (
        "INVESTABILITY: CONTINUE — draft\n"
        "...\n"
        "INVESTABILITY: STOP — revised after reading 10-K footnotes\n"
    )
    decision, reason = parse_investability(text)
    assert decision == "STOP"
    assert "footnotes" in reason


def test_parse_malformed_missing():
    text = "Just a body with no verdict line."
    decision, reason = parse_investability(text)
    assert decision == "MALFORMED"
    assert "no INVESTABILITY" in reason


def test_parse_malformed_bad_verdict():
    text = "INVESTABILITY: MAYBE — hedged"
    decision, _ = parse_investability(text)
    # The regex rejects non-CONTINUE/STOP verdicts → MALFORMED
    assert decision == "MALFORMED"


def test_parse_empty():
    assert parse_investability("")[0] == "MALFORMED"
    assert parse_investability(None)[0] == "MALFORMED"  # type: ignore[arg-type]
