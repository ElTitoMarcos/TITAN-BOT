from __future__ import annotations

"""Time formatting helpers."""

from datetime import datetime
from typing import Any, Union
from zoneinfo import ZoneInfo

__all__ = ["fmt_ts"]


_MIN_VALID_MS = 946684800000  # 2000-01-01 00:00:00 UTC


def _to_datetime(ts: Any, tz: ZoneInfo) -> datetime | None:
    """Convert ``ts`` to a timezone-aware ``datetime``.

    Supported inputs:
    - seconds or milliseconds since epoch (``int``/``float`` or numeric ``str``)
    - ISO 8601 strings (with optional timezone).
    """
    if ts is None:
        return None

    # Allow strings that may contain numeric values
    if isinstance(ts, str):
        ts = ts.strip()
        if not ts:
            return None
        # Numeric string
        try:
            ts_num = float(ts)
            ts = ts_num
        except ValueError:
            # ISO format
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            else:
                dt = dt.astimezone(tz)
            return dt

    if isinstance(ts, (int, float)):
        # Determine if ts is in milliseconds
        ts_ms = float(ts)
        if ts_ms > 1e12:  # assume milliseconds
            ts_ms = ts_ms
        else:
            ts_ms = ts_ms * 1000
        if ts_ms < _MIN_VALID_MS:
            return None
        try:
            return datetime.fromtimestamp(ts_ms / 1000, tz)
        except Exception:
            return None

    return None


def fmt_ts(ts: Any, tz: str = "Europe/Madrid") -> str:
    """Format various timestamp inputs as ``YYYY-MM-DD HH:MM:SS``.

    Parameters
    ----------
    ts:
        Timestamp in seconds, milliseconds or ISO-8601 string.
    tz:
        Target timezone. Defaults to ``Europe/Madrid``.
    """
    zone = ZoneInfo(tz)
    dt = _to_datetime(ts, zone)
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M:%S")
