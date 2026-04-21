from __future__ import annotations

import pytest

from services.mcp import server


@pytest.mark.asyncio
async def test_open_investigation_rejects_theme_scope_until_supported():
    result = await server.open_investigation(theme="ai-capex")
    assert result["ok"] is False
    assert "not yet supported" in result["error"]
