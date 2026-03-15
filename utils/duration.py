"""
utils/duration.py — Human-readable duration parsing and formatting.

Supported format: <number><unit>[<number><unit>...]
Units: w (weeks), d (days), h (hours), m (minutes), s (seconds)

Examples:
    "2w3d"    → 17 days
    "6h30m"   → 6 hours 30 minutes
    "1d"      → 1 day
    "permanent" | "perm" | "indefinite" → None (no release)
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional

# Tokens that signal a permanent / indefinite sentence
_PERMANENT_TOKENS = {"permanent", "perm", "indefinite", "forever", "inf"}

# Regex to extract unit/value pairs
_UNIT_RE = re.compile(r"(\d+)\s*([wdhms])", re.IGNORECASE)

_UNIT_SECONDS = {
    "w": 7 * 24 * 3600,
    "d": 24 * 3600,
    "h": 3600,
    "m": 60,
    "s": 1,
}


def parse_duration(text: str) -> Optional[timedelta]:
    """
    Parse a duration string into a timedelta.

    Returns None if the text represents a permanent/indefinite sentence.
    Raises ValueError if the string cannot be parsed.
    """
    stripped = text.strip().lower()

    if stripped in _PERMANENT_TOKENS:
        return None

    matches = _UNIT_RE.findall(stripped)
    if not matches:
        raise ValueError(
            f"Could not parse duration `{text}`. "
            "Use a format like `2d6h30m`, `1w`, or `permanent`."
        )

    total_seconds = 0
    for value_str, unit in matches:
        total_seconds += int(value_str) * _UNIT_SECONDS[unit.lower()]

    if total_seconds <= 0:
        raise ValueError("Duration must be greater than zero.")

    return timedelta(seconds=total_seconds)


def format_timedelta(td: timedelta) -> str:
    """Format a timedelta into a human-readable string like '2d 6h 30m'."""
    return format_seconds(int(td.total_seconds()))


def format_seconds(seconds: int) -> str:
    """Format a number of seconds into a human-readable string like '2d 6h 30m'."""
    if seconds <= 0:
        return "0s"

    parts: list[str] = []
    weeks, seconds = divmod(seconds, 7 * 24 * 3600)
    days, seconds = divmod(seconds, 24 * 3600)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    if weeks:
        parts.append(f"{weeks}w")
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds:
        parts.append(f"{seconds}s")

    return " ".join(parts) if parts else "0s"
