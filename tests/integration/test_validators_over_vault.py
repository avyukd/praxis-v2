from __future__ import annotations

import json
from pathlib import Path

from praxis_core.tasks.validators import validate_lint_vault, validate_refresh_index


def test_refresh_index_writes_file(tmp_path: Path) -> None:
    # Simulate what refresh_index handler would do
    index = tmp_path / "INDEX.md"
    index.write_text("# INDEX\n\nauto-generated.\n")
    r = validate_refresh_index({"scope": "full", "triggered_by": "scheduler"}, tmp_path)
    assert r.is_success


def test_lint_vault_checks_for_report(tmp_path: Path) -> None:
    r = validate_lint_vault({"triggered_by": "scheduler"}, tmp_path)
    assert not r.is_success
