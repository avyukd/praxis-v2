"""Pre-write backup stash for vault files — D38."""

from __future__ import annotations

import shutil
from pathlib import Path

from praxis_core.time_et import now_et
from praxis_core.vault.writer import atomic_write


def stash_for_edit(path: Path, vault_root: Path, category: str = "compile") -> Path | None:
    """Copy path's current content (if any) to _backups/<category>/<date>/<et-HHMMSS>-<ticker>-<name>.

    Returns the backup path, or None if the source doesn't exist.
    """
    if not path.exists():
        return None
    dt = now_et()
    date_str = dt.strftime("%Y-%m-%d")
    stamp = dt.strftime("%H%M%S")
    # Flatten to a recognizable filename (ticker-notes, ticker-journal, etc.)
    try:
        rel = path.relative_to(vault_root)
    except ValueError:
        rel = Path(path.name)
    flat = str(rel).replace("/", "-")
    backup_dir = vault_root / "_backups" / category / date_str
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{stamp}-{flat}"
    shutil.copy2(path, backup_path)
    return backup_path
