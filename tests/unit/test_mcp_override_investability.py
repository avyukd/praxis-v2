"""Structural test — override_investability tool is registered."""

from __future__ import annotations

from services.mcp import server


def test_override_investability_exported():
    # Registered via @mcp.tool() and surfaces as module attribute.
    assert hasattr(server, "override_investability"), (
        "D20: override_investability MCP tool must be registered on the server module"
    )


def test_override_investability_callable():
    # FastMCP wraps the original function; ensure the registered object is
    # callable and the symbol is on the module.
    assert callable(server.override_investability)
