from __future__ import annotations

from datetime import UTC, datetime

from praxis_core.time_et import ET, et_date_str, et_iso, now_et, now_utc, to_et


def test_now_et_is_aware() -> None:
    n = now_et()
    assert n.tzinfo is ET


def test_now_utc_is_aware() -> None:
    n = now_utc()
    assert n.tzinfo is UTC


def test_to_et_handles_naive_as_utc() -> None:
    naive = datetime(2026, 4, 18, 16, 0)
    et = to_et(naive)
    # April 18 is EDT (UTC-4); 16:00 UTC = 12:00 ET
    assert et.hour == 12
    assert et.date().isoformat() == "2026-04-18"


def test_to_et_crosses_day_boundary() -> None:
    # 02:00 UTC on Apr 18 EDT = 22:00 ET Apr 17
    dt = datetime(2026, 4, 18, 2, 0, tzinfo=UTC)
    et = to_et(dt)
    assert et.date().isoformat() == "2026-04-17"
    assert et.hour == 22


def test_et_date_str_defaults_to_now() -> None:
    s = et_date_str()
    assert len(s) == 10
    assert s[4] == "-" and s[7] == "-"


def test_et_date_str_with_aware_dt() -> None:
    dt = datetime(2026, 4, 18, 15, 0, tzinfo=ET)
    assert et_date_str(dt) == "2026-04-18"


def test_et_iso_roundtrip() -> None:
    dt = datetime(2026, 4, 18, 15, 0, tzinfo=ET)
    assert "2026-04-18T15:00" in et_iso(dt)


def test_dst_aware() -> None:
    # January is EST (UTC-5)
    jan = datetime(2026, 1, 15, 17, 0, tzinfo=UTC)
    assert to_et(jan).hour == 12
    # June is EDT (UTC-4)
    jun = datetime(2026, 6, 15, 17, 0, tzinfo=UTC)
    assert to_et(jun).hour == 13
