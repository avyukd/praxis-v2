from __future__ import annotations

import os
from pathlib import Path

import pytest

from praxis_core.vault.writer import (
    append_atomic,
    atomic_write,
    write_markdown_with_frontmatter,
)


def test_atomic_write_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "file.md"
    atomic_write(target, "hello")
    assert target.read_text() == "hello"


def test_atomic_write_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "file.md"
    atomic_write(target, "first")
    atomic_write(target, "second")
    assert target.read_text() == "second"


def test_atomic_write_leaves_no_tempfile_on_success(tmp_path: Path) -> None:
    target = tmp_path / "file.md"
    atomic_write(target, "hello")
    residual = [p for p in tmp_path.iterdir() if p.name.startswith(".")]
    assert residual == []


def test_atomic_write_cleans_up_tempfile_on_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "file.md"

    original_replace = os.replace

    def failing_replace(src: str, dst: str) -> None:
        raise OSError("simulated failure")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError):
        atomic_write(target, "hello")

    monkeypatch.setattr(os, "replace", original_replace)
    residual = list(tmp_path.iterdir())
    assert residual == []


def test_atomic_write_binary(tmp_path: Path) -> None:
    target = tmp_path / "file.bin"
    atomic_write(target, b"\x00\x01\x02", mode="wb")
    assert target.read_bytes() == b"\x00\x01\x02"


def test_append_atomic_starts_empty(tmp_path: Path) -> None:
    target = tmp_path / "log.md"
    append_atomic(target, "line 1\n")
    append_atomic(target, "line 2\n")
    assert target.read_text() == "line 1\nline 2\n"


def test_write_markdown_with_frontmatter(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    write_markdown_with_frontmatter(
        target,
        body="# Hello\n\nBody text.",
        metadata={"type": "memo", "status": "active", "tags": ["test"]},
    )
    text = target.read_text()
    assert text.startswith("---\n")
    assert "type: memo" in text
    assert "status: active" in text
    assert "data_vintage:" in text
    assert "# Hello" in text


def test_write_markdown_preserves_explicit_data_vintage(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    write_markdown_with_frontmatter(
        target,
        body="x",
        metadata={"type": "memo", "data_vintage": "2020-01-01"},
    )
    text = target.read_text()
    assert "data_vintage: '2020-01-01'" in text or "data_vintage: 2020-01-01" in text
