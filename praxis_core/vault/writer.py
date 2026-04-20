from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import yaml

from praxis_core.time_et import et_date_str


def atomic_write(path: Path | str, content: str | bytes, *, mode: str = "w") -> None:
    """Write `content` to `path` atomically via tempfile + rename.

    Creates parent directories as needed. The tempfile is a dotfile in the same
    directory (same filesystem as the target, so `os.replace` is atomic). On any
    failure the tempfile is removed so the caller never sees partial state.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        if "b" in mode:
            with open(tmp, mode) as f:
                f.write(content)  # type: ignore[arg-type]
                f.flush()
                os.fsync(f.fileno())
        else:
            with open(tmp, mode, encoding="utf-8") as f:
                f.write(content)  # type: ignore[arg-type]
                f.flush()
                os.fsync(f.fileno())
        os.replace(str(tmp), str(target))
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def append_atomic(path: Path | str, content: str) -> None:
    """Append `content` to `path`, preserving crash safety via read + atomic rewrite.

    Not safe under concurrent writers — callers must serialize (praxis-v2 does this
    via `resource_key` mutexes in the dispatcher).
    """
    target = Path(path)
    existing = ""
    if target.exists():
        existing = target.read_text(encoding="utf-8")
    atomic_write(target, existing + content)


def write_markdown_with_frontmatter(
    path: Path | str,
    *,
    body: str,
    metadata: dict[str, Any],
) -> None:
    """Write a markdown file with YAML frontmatter, atomically.

    Auto-fills `data_vintage` with today's ET date if the caller didn't supply it.
    """
    meta = dict(metadata)
    if "data_vintage" not in meta:
        meta["data_vintage"] = et_date_str()
    fm = yaml.safe_dump(
        meta,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    ).rstrip("\n")
    content = f"---\n{fm}\n---\n\n{body}\n"
    atomic_write(path, content)
