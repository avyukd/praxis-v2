from __future__ import annotations

from praxis_core.filters.edgar_items import (
    LONG_ITEMS,
    extract_items_from_summary,
    items_pass_allowlist,
)


def test_extract_items_from_real_summary() -> None:
    summary = (
        "<b>Filed:</b> 2026-04-17 <b>AccNo:</b> 0001213900-26-045267 <b>Size:</b> 201 KB"
        "<br>Item 3.01: Notice of Delisting"
        "<br>Item 9.01: Financial Statements and Exhibits"
    )
    items = extract_items_from_summary(summary)
    assert items == ["3.01", "9.01"]


def test_extract_items_from_multi_item_8k() -> None:
    summary = (
        "Item 1.01: Material Agreement<br>Item 2.03: Financial Obligation<br>Item 9.01: Exhibits"
    )
    assert extract_items_from_summary(summary) == ["1.01", "2.03", "9.01"]


def test_extract_items_empty_summary() -> None:
    assert extract_items_from_summary("") == []
    assert extract_items_from_summary("Item X.YZ") == []  # non-numeric
    assert extract_items_from_summary("no items here") == []


def test_extract_items_dedupes_preserving_order() -> None:
    s = "Item 2.02: Earnings<br>Item 7.01: FD<br>Item 2.02: (again)"
    assert extract_items_from_summary(s) == ["2.02", "7.01"]


def test_allowlist_accepts_material_agreement() -> None:
    passes, matched = items_pass_allowlist(["1.01", "9.01"])
    assert passes
    assert matched == {"1.01"}


def test_allowlist_rejects_delisting_only() -> None:
    # 3.01 is NOT in LONG_ITEMS. 9.01 alone isn't either.
    passes, matched = items_pass_allowlist(["3.01", "9.01"])
    assert not passes
    assert matched == set()


def test_allowlist_accepts_earnings() -> None:
    passes, matched = items_pass_allowlist(["2.02"])
    assert passes
    assert matched == {"2.02"}


def test_allowlist_accepts_executive_departure() -> None:
    passes, matched = items_pass_allowlist(["5.02", "9.01"])
    assert passes
    assert matched == {"5.02"}


def test_custom_allowlist() -> None:
    passes, matched = items_pass_allowlist(["3.01"], allowlist={"3.01"})
    assert passes
    assert matched == {"3.01"}


def test_long_items_contains_expected() -> None:
    # Spot-check the canonical set.
    for code in ("1.01", "2.02", "5.02", "8.01"):
        assert code in LONG_ITEMS
    # 3.01 (delisting) and 5.07 (vote) intentionally excluded from LONG_ITEMS
    assert "3.01" not in LONG_ITEMS
    assert "5.07" not in LONG_ITEMS
