from __future__ import annotations

from pathlib import Path

from praxis_core.vault.section_append import append_to_section


def test_creates_file_and_section(tmp_path: Path) -> None:
    p = tmp_path / "themes" / "uranium.md"
    appended = append_to_section(p, "## Surfaced ideas", "first bullet")
    assert appended is True
    text = p.read_text()
    assert text.startswith("## Surfaced ideas\n\n")
    assert "- first bullet" in text


def test_appends_to_existing_section(tmp_path: Path) -> None:
    p = tmp_path / "uranium.md"
    p.write_text("# Uranium\n\n## Surfaced ideas\n\n- existing bullet\n")
    appended = append_to_section(p, "## Surfaced ideas", "new bullet")
    assert appended is True
    text = p.read_text()
    assert "- existing bullet" in text
    assert "- new bullet" in text


def test_dedup_skips_if_substring_present(tmp_path: Path) -> None:
    p = tmp_path / "uranium.md"
    p.write_text("## Surfaced ideas\n\n- existing — batch:20260420-1430\n")
    appended = append_to_section(
        p,
        "## Surfaced ideas",
        "new bullet — batch:20260420-1430",
        dedup_substring="batch:20260420-1430",
    )
    assert appended is False


def test_adds_new_section_if_missing(tmp_path: Path) -> None:
    p = tmp_path / "uranium.md"
    p.write_text("# Uranium\n\n## Existing\n\n- content\n")
    appended = append_to_section(p, "## Surfaced ideas", "first surface")
    assert appended is True
    text = p.read_text()
    assert "## Existing" in text
    assert "## Surfaced ideas" in text
    assert "- first surface" in text


def test_does_not_cross_section_boundary(tmp_path: Path) -> None:
    p = tmp_path / "uranium.md"
    p.write_text("## Surfaced ideas\n\n- a\n\n## Notes\n\n- b\n")
    append_to_section(p, "## Surfaced ideas", "c")
    text = p.read_text()
    # "c" should appear under Surfaced ideas, before Notes
    surfaced_idx = text.find("## Surfaced ideas")
    notes_idx = text.find("## Notes")
    c_idx = text.find("- c")
    assert surfaced_idx < c_idx < notes_idx
