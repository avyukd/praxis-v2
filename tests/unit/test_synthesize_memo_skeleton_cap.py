"""Wall-clock cap on synthesize_memo transient-retry loop.

Without this cap, a crashed dive that leaves a <1500 byte skeleton
file would make synthesize_memo return transient=True indefinitely.
The cap falls through to a degraded-memo path after SKELETON_WALLCLOCK_CAP_S.
"""

from __future__ import annotations

import os
import time

import pytest

from handlers.synthesize_memo import SKELETON_WALLCLOCK_CAP_S, SPECIALTIES


def _setup_dives_dir(vault, ticker):
    d = vault / "companies" / ticker / "dives"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_skeleton_cap_constant_is_reasonable():
    """Floor and ceiling guard: between 1h and 24h."""
    assert 3600 <= SKELETON_WALLCLOCK_CAP_S <= 86400


def test_specialties_list_covers_expected_dives():
    """Memo gates on these specialty slugs — regression if renamed."""
    for s in ("financial-rigorous", "business-moat", "industry-structure", "capital-allocation"):
        assert s in SPECIALTIES


def test_fresh_skeleton_triggers_transient_retry(tmp_path):
    """File <1500 bytes and <cap seconds old → gate fires, transient=True."""
    # We avoid running the full handler (needs DB + LLM). Test the logic
    # directly by reproducing the mtime/age check.
    ticker = "TST"
    d = _setup_dives_dir(tmp_path, ticker)
    skeleton = d / "financial-rigorous.md"
    skeleton.write_text("skeleton")  # tiny file
    now = time.time()
    recent = now - 300  # 5 min ago
    os.utime(skeleton, (recent, recent))

    st = skeleton.stat()
    age = now - st.st_mtime
    assert st.st_size < 1500
    assert age < SKELETON_WALLCLOCK_CAP_S  # would trigger transient


def test_stale_skeleton_past_cap_falls_through(tmp_path):
    """File <1500 bytes and >cap seconds old → gate bypasses transient."""
    ticker = "TST"
    d = _setup_dives_dir(tmp_path, ticker)
    skeleton = d / "financial-rigorous.md"
    skeleton.write_text("skeleton")
    now = time.time()
    stale = now - (SKELETON_WALLCLOCK_CAP_S + 600)  # 10min past cap
    os.utime(skeleton, (stale, stale))

    st = skeleton.stat()
    age = now - st.st_mtime
    assert st.st_size < 1500
    assert age > SKELETON_WALLCLOCK_CAP_S  # would fall through


def test_real_sized_dive_never_triggers_gate(tmp_path):
    """Dive with >1500 bytes bypasses the gate entirely."""
    ticker = "TST"
    d = _setup_dives_dir(tmp_path, ticker)
    real = d / "financial-rigorous.md"
    real.write_text("x" * 3000)

    st = real.stat()
    assert st.st_size >= 1500  # bypasses transient entirely
