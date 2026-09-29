"""Timestamps: one way to record them, and friendly ways to show them.

All times are STORED in UTC as ISO strings without a zone, e.g.
"2026-09-24T21:40:12" (that's what the app has always written).

For DISPLAY, templates use two filters (registered in main.py):
    {{ jd.created_at|ago }}      → "3 days ago"
    {{ jd.created_at|iso_utc }}  → "2026-09-24T21:40:12Z" (for <time datetime=...>)
The browser then converts exact times to the viewer's local time zone
(see the small script at the end of base.html), so a recruiter in New York and
a candidate in Pune each see their own local time.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def utc_now_iso() -> str:
    """Current UTC time as an ISO string (seconds precision), for saving."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def parse_utc(value) -> Optional[datetime]:
    """Parse a stored timestamp into an aware UTC datetime, or None if blank/invalid."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def iso_utc(value) -> str:
    """'2026-09-24T21:40:12Z' — unambiguous UTC for HTML <time datetime="...">."""
    dt = parse_utc(value)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""


def utc_label(value, with_time: bool = True) -> str:
    """Readable UTC fallback shown before the browser converts it, e.g. 'Sep 24, 2026, 21:40 UTC'."""
    dt = parse_utc(value)
    if not dt:
        return ""
    date = f"{dt:%b} {dt.day}, {dt.year}"
    return f"{date}, {dt:%H:%M} UTC" if with_time else date


def time_ago(value, now: Optional[datetime] = None) -> str:
    """Relative time like 'just now', '5 minutes ago', 'yesterday', '3 weeks ago'.

    Args:
        value: A stored timestamp (ISO string or datetime).
        now: Override "now" (used by tests); defaults to the current UTC time.
    """
    dt = parse_utc(value)
    if not dt:
        return ""
    now = parse_utc(now) if now else datetime.now(timezone.utc)
    seconds = int((now - dt).total_seconds())

    if seconds < 0:          # clock skew / future date
        return "just now"

    def plural(n: int, unit: str) -> str:
        return f"{n} {unit}{'' if n == 1 else 's'} ago"

    minutes, hours, days = seconds // 60, seconds // 3600, seconds // 86400
    if seconds < 60:
        return "just now"
    if minutes < 60:
        return plural(minutes, "minute")
    if hours < 24:
        return plural(hours, "hour")
    if days == 1:
        return "yesterday"
    if days < 7:
        return plural(days, "day")
    if days < 30:
        return plural(days // 7, "week")
    if days < 365:
        return plural(days // 30, "month")
    return plural(days // 365, "year")
