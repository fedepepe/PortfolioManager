"""Conversions between Degiro values and database columns."""

from datetime import datetime


def list_to_str(lst: list[str], sep: str = ', ') -> str | None:
    """Join a list into one string (None stays None)."""
    return sep.join(lst) if lst is not None else None


def str_to_date(s: str) -> datetime | None:
    """Parse a YYYY-MM-DD date (None stays None)."""
    return datetime.strptime(s, '%Y-%m-%d') if s is not None else None
