from __future__ import annotations

from typing import Any

import pytest

from services.mcp import server as mcp_server


def test_main_defaults_to_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []

    monkeypatch.delenv("PRAXIS_MCP_TRANSPORT", raising=False)
    monkeypatch.delenv("PRAXIS_MCP_MOUNT_PATH", raising=False)
    monkeypatch.setattr(mcp_server, "configure_logging", lambda: None)
    monkeypatch.setattr(mcp_server.log, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(mcp_server.mcp, "run", lambda *args, **kwargs: calls.append(args))

    mcp_server.main()

    assert calls == [("stdio",)]


def test_main_uses_streamable_http_transport_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []

    monkeypatch.setenv("PRAXIS_MCP_TRANSPORT", "streamable-http")
    monkeypatch.delenv("PRAXIS_MCP_MOUNT_PATH", raising=False)
    monkeypatch.setattr(mcp_server, "configure_logging", lambda: None)
    monkeypatch.setattr(mcp_server.log, "info", lambda *args, **kwargs: None)
    monkeypatch.setattr(mcp_server.mcp, "run", lambda *args, **kwargs: calls.append(args))

    mcp_server.main()

    assert calls == [("streamable-http",)]
