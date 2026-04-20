"""End-to-end mini migration test over a fake vault."""

from __future__ import annotations

from pathlib import Path

from services.migrate.vault_migrator import apply, plan


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _fake_vault(root: Path) -> None:
    _write(
        root / "10_themes/strait-of-hormuz.md",
        "---\ntype: theme\nstatus: active\n---\n\n# Hormuz\n\nSee [[20_companies/NVDA/notes]] too.\n",
    )
    _write(
        root / "15_concepts/circle-of-competence.md",
        "---\ntype: concept\nstatus: active\n---\n\n# CoC\n\nRef [[buffett-warren]].\n",
    )
    _write(
        root / "25_people/buffett-warren.md",
        "---\ntype: person\nstatus: active\n---\n\n# Warren Buffett\n",
    )
    _write(
        root / "20_companies/NVDA/notes.md",
        "---\ntype: company_note\nticker: NVDA\nstatus: active\n---\n\n# NVDA notes\n\nRef [[strait-of-hormuz]].\n",
    )
    _write(
        root / "20_companies/NVDA/journal.md",
        "- 2026-04-10: initial\n",
    )
    _write(
        root / "30_theses/nvda-ai-capex.md",
        "---\ntype: thesis\nticker: NVDA\nstatus: active\n---\n\n# NVDA thesis\n\nBody v1.\n",
    )
    _write(
        root / "40_memos/2026-04-10-nvda-thesis.md",
        "---\ntype: memo\nticker: NVDA\nstatus: final\n---\n\n# Memo\n\n[[20_companies/NVDA/notes]]\n",
    )
    _write(
        root / "40_memos/2026-04-10-hormuz-scenarios.md",
        "---\ntype: memo\nstatus: final\n---\n\n# Hormuz memo\n",
    )
    _write(
        root / "60_questions/argx-deep-dive.md",
        "---\ntype: question\nstatus: answered\n---\n\n# Q\n\nsee [[circle-of-competence]]\n",
    )
    _write(
        root / "80_sources/2026/04/2026-04-18_ft.com_article.md",
        "---\ntype: source\n---\n\nclipped article body\n",
    )
    _write(root / "00_inbox/capture.md", "drop this")
    _write(root / "90_meta/agenda.md", "drop this too")
    _write(root / "INDEX.md", "drop me")


def test_plan_surfaces_everything(tmp_path: Path) -> None:
    src = tmp_path / "source"
    _fake_vault(src)
    target = tmp_path / "staging"

    _, report = plan(src, target)
    assert report.files_written > 0
    assert report.files_dropped >= 3  # inbox, agenda, INDEX
    assert len(report.unresolved_wikilinks) == 0
    # Thesis merge tracked
    assert "NVDA" in report.thesis_merges
    # Memo re-nest tracked
    assert "NVDA" in report.memo_rensests


def test_apply_executes_migration(tmp_path: Path) -> None:
    src = tmp_path / "source"
    _fake_vault(src)
    target = tmp_path / "staging"

    report = apply(src, target)

    # Files should exist at their new locations
    assert (target / "themes/strait-of-hormuz.md").exists()
    assert (target / "concepts/circle-of-competence.md").exists()
    assert (target / "people/buffett-warren.md").exists()
    assert (target / "companies/NVDA/notes.md").exists()
    assert (target / "companies/NVDA/journal.md").exists()
    assert (target / "companies/NVDA/thesis.md").exists()  # merged
    assert (target / "companies/NVDA/memos/2026-04-10-nvda-thesis.md").exists()
    assert (target / "memos/2026-04-10-hormuz-scenarios.md").exists()
    assert (target / "questions/argx-deep-dive.md").exists()
    assert (target / "_raw/desktop_clips/2026-04-18/ft.com_article.md").exists()

    # Dropped files should NOT exist
    assert not (target / "00_inbox/capture.md").exists()
    assert not (target / "90_meta/agenda.md").exists()
    # D55: source INDEX.md is dropped but target gets a fresh one from
    # vault_seed/INDEX.md on migration apply — verify it's the seed version
    assert (target / "INDEX.md").exists()
    assert "drop me" not in (target / "INDEX.md").read_text()

    # Wikilinks should be rewritten
    notes_text = (target / "companies/NVDA/notes.md").read_text()
    assert "[[themes/strait-of-hormuz]]" in notes_text
    assert "[[20_companies/" not in notes_text

    q_text = (target / "questions/argx-deep-dive.md").read_text()
    assert "[[concepts/circle-of-competence]]" in q_text

    # Memo status 'final' → 'resolved'
    memo_text = (target / "memos/2026-04-10-hormuz-scenarios.md").read_text()
    assert "status: resolved" in memo_text

    # Thesis merge includes frontmatter marker
    thesis_text = (target / "companies/NVDA/thesis.md").read_text()
    assert "type: thesis" in thesis_text
    assert "ticker: NVDA" in thesis_text
    assert "merged_from" in thesis_text
    assert "Body v1" in thesis_text

    # Audit trail
    theme_text = (target / "themes/strait-of-hormuz.md").read_text()
    assert "migrated_from: autoresearch" in theme_text

    # Zero unresolved links (we tied up every reference in the fake vault)
    assert len(report.unresolved_wikilinks) == 0
