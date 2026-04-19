from __future__ import annotations

from services.migrate.rename_map import RenameEntry, RenameMap
from services.migrate.wikilinks import rewrite_body


def _map() -> RenameMap:
    rm = RenameMap()
    rm.add(
        RenameEntry(
            old_path="10_themes/strait-of-hormuz.md",
            new_path="themes/strait-of-hormuz.md",
            kind="theme",
        )
    )
    rm.add(
        RenameEntry(
            old_path="20_companies/NVDA/notes.md",
            new_path="companies/NVDA/notes.md",
            kind="company_note",
        )
    )
    rm.add(
        RenameEntry(
            old_path="40_memos/2026-04-10-clmt-rfs.md",
            new_path="companies/CLMT/memos/2026-04-10-clmt-rfs.md",
            kind="memo",
        )
    )
    return rm


def test_basic_rewrite() -> None:
    body = "See [[10_themes/strait-of-hormuz]] for context."
    r = rewrite_body(body, _map())
    assert r.new_body == "See [[themes/strait-of-hormuz]] for context."
    assert r.rewrote == 1
    assert r.unresolved == []


def test_alias_preserved() -> None:
    body = "See [[10_themes/strait-of-hormuz|Hormuz theme]]"
    r = rewrite_body(body, _map())
    assert r.new_body == "See [[themes/strait-of-hormuz|Hormuz theme]]"
    assert r.rewrote == 1


def test_heading_anchor_preserved() -> None:
    body = "[[20_companies/NVDA/notes#Moat]]"
    r = rewrite_body(body, _map())
    assert r.new_body == "[[companies/NVDA/notes#Moat]]"


def test_heading_and_alias() -> None:
    body = "[[20_companies/NVDA/notes#Moat|NVDA moat]]"
    r = rewrite_body(body, _map())
    assert r.new_body == "[[companies/NVDA/notes#Moat|NVDA moat]]"


def test_stem_only_match() -> None:
    body = "Short form: [[strait-of-hormuz]]"
    r = rewrite_body(body, _map())
    assert r.new_body == "Short form: [[themes/strait-of-hormuz]]"


def test_dead_targets_get_stripped() -> None:
    body = (
        "[[10_themes/strait-of-hormuz]] and [[20_companies/NVDA/notes]] "
        "but [[90_meta/agenda]] is gone and [[00_inbox/thing]] too."
    )
    r = rewrite_body(body, _map())
    assert "[[themes/strait-of-hormuz]]" in r.new_body
    assert "[[companies/NVDA/notes]]" in r.new_body
    # Dead refs: brackets stripped, bare text preserved
    assert "[[90_meta/agenda]]" not in r.new_body
    assert "90_meta/agenda" in r.new_body
    assert "00_inbox/thing" in r.new_body
    assert r.rewrote == 2
    assert r.stripped_dead == 2
    assert r.unresolved == []


def test_dead_target_with_alias_keeps_display_text() -> None:
    body = "see [[90_meta/agenda|the agenda]] for more"
    r = rewrite_body(body, _map())
    assert "[[" not in r.new_body
    assert "the agenda" in r.new_body
    assert r.stripped_dead == 1


def test_truly_unresolved_left_alone() -> None:
    body = "[[some-random-target-not-in-map]]"
    r = rewrite_body(body, _map())
    assert "[[some-random-target-not-in-map]]" in r.new_body
    assert r.unresolved == ["some-random-target-not-in-map"]
    assert r.stripped_dead == 0


def test_with_md_extension() -> None:
    body = "[[10_themes/strait-of-hormuz.md]]"
    r = rewrite_body(body, _map())
    assert r.new_body == "[[themes/strait-of-hormuz]]"


def test_memo_rewrite() -> None:
    body = "[[40_memos/2026-04-10-clmt-rfs]]"
    r = rewrite_body(body, _map())
    assert "[[companies/CLMT/memos/2026-04-10-clmt-rfs]]" in r.new_body
