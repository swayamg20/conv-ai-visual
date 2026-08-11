"""Timestamp helpers for the existing SQLite persistence contract."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return UTC as a naive datetime, matching the legacy SQLite column format."""
    return datetime.now(UTC).replace(tzinfo=None)
