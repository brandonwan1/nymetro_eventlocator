"""Date/time labels without platform-specific strftime codes (%-d / %-I don't exist on Windows)."""

from __future__ import annotations

from datetime import datetime


def fmt_day(dt: datetime) -> str:
    """'Sat Sep 26'"""
    return f"{dt:%a %b} {dt.day}"


def fmt_time(dt: datetime) -> str:
    """'9:00 PM'"""
    return f"{(dt.hour % 12) or 12}:{dt:%M %p}"


def fmt_day_time(dt: datetime) -> str:
    """'Sat Sep 26, 9:00 PM'"""
    return f"{fmt_day(dt)}, {fmt_time(dt)}"
