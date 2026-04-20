"""Structural checks for Section C MCP tools.

Heavier SQL-path integration tests for cancel_investigation's cascade
+ list_investigations task counts live in tests/integration/ (added as
follow-up). These unit tests just verify the module surface:
  * pause_investigation / resume_investigation are deleted (D32)
  * cancel_investigation is defined (D33)
  * list_investigations is defined (D34)
"""

from __future__ import annotations


def test_pause_resume_removed() -> None:
    from services.mcp import server

    assert not hasattr(server, "pause_investigation"), "D32: pause_investigation must be deleted"
    assert not hasattr(
        server, "resume_investigation"
    ), "D32: resume_investigation must be deleted"


def test_cancel_investigation_defined() -> None:
    from services.mcp import server

    assert hasattr(server, "cancel_investigation"), "D33: cancel_investigation must exist"


def test_list_investigations_defined() -> None:
    from services.mcp import server

    assert hasattr(server, "list_investigations"), "D34: list_investigations must exist"
