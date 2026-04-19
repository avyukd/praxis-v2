from __future__ import annotations

from services.migrate.frontmatter import normalize_metadata, process_markdown, serialize


def test_memo_final_becomes_resolved() -> None:
    out = normalize_metadata({"type": "memo", "status": "final"}, source_label="autoresearch")
    assert out["status"] == "resolved"
    assert out["migrated_from"] == "autoresearch"
    assert "migrated_at" in out


def test_non_memo_status_preserved() -> None:
    out = normalize_metadata({"type": "concept", "status": "active"})
    assert out["status"] == "active"


def test_preserves_unknown_fields() -> None:
    out = normalize_metadata(
        {
            "type": "company_note",
            "created_by_focus": "argx-deep-dive",
            "scores": {"tactical": 7, "fundamental": 8},
            "preliminary_decision": "Buy",
        }
    )
    assert out["created_by_focus"] == "argx-deep-dive"
    assert out["scores"] == {"tactical": 7, "fundamental": 8}
    assert out["preliminary_decision"] == "Buy"


def test_process_and_serialize_roundtrip() -> None:
    original = (
        "---\n"
        "type: memo\n"
        "ticker: NVDA\n"
        "status: final\n"
        "data_vintage: 2026-04-10\n"
        "---\n\n"
        "# Body text\n\nMore content.\n"
    )
    meta, body = process_markdown(original)
    assert meta["status"] == "resolved"  # remapped
    assert meta["type"] == "memo"
    assert meta["ticker"] == "NVDA"
    assert meta["migrated_from"] == "autoresearch"

    result = serialize(meta, body)
    assert result.startswith("---\n")
    assert "type: memo" in result
    assert "status: resolved" in result
    assert "# Body text" in result
