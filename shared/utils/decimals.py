"""Decimal and parsing helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


def dec(value) -> Decimal | None:
    """Parse a value to Decimal, returning None on failure."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def dec_str(value) -> str | None:
    """Return stripped string or None."""
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def parse_iso(value) -> str | None:
    """Parse an ISO timestamp string, return ISO format or None."""
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.isoformat()
    except ValueError:
        return None


def unix_s_to_iso(ts) -> str | None:
    """Convert a Unix timestamp (seconds) to ISO string."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return None


def json_loads(value) -> list | dict | None:
    """Parse JSON string, passthrough if already parsed."""
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
