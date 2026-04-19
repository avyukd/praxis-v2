"""Timezone utilities — EVERYTHING user-facing lives in ET.

Core principle: Postgres stores timestamps in UTC (standard practice, no info loss),
but any date boundary, display, file/path naming, frontmatter, log text, or
cadence decision is based on ET.

Do NOT use datetime.utcnow() or timezone.utc directly except for DB `server_default=func.now()`
which Postgres renders in UTC. Use `now_et()` for "what time is it" / "what day is it".
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def now_et() -> datetime:
    """Current wall-clock time in ET (America/New_York), DST-aware."""
    return datetime.now(ET)


def now_utc() -> datetime:
    """Current time in UTC — reserved for DB persistence only."""
    return datetime.now(UTC)


def to_et(dt: datetime) -> datetime:
    """Convert any (aware or naive-UTC) datetime to ET."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ET)


def et_date_str(dt: datetime | None = None) -> str:
    """ET calendar date as YYYY-MM-DD. Default: today in ET."""
    d = to_et(dt) if dt is not None else now_et()
    return d.strftime("%Y-%m-%d")


def et_iso(dt: datetime | None = None) -> str:
    """ISO-8601 timestamp in ET."""
    d = to_et(dt) if dt is not None else now_et()
    return d.isoformat()
