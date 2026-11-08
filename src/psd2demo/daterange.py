"""Transaction date-range resolution for --days / --from / --to."""

import argparse
from datetime import date, timedelta

from psd2demo.errors import UsageError


def iso_date(value: str) -> date:
    """argparse type for YYYY-MM-DD."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}; expected YYYY-MM-DD")


def resolve_range(today: date, *, days: int | None = None, date_from: date | None = None,
                  date_to: date | None = None, default_days: int) -> tuple[date, date]:
    if days is not None and (date_from or date_to):
        raise UsageError("use either --days or --from/--to, not both")
    if days is not None and days < 1:
        raise UsageError("--days must be at least 1")
    date_to = date_to or today
    if date_to > today:
        raise UsageError(f"--to {date_to} is in the future")
    date_from = date_from or date_to - timedelta(days=days or default_days)
    if date_from > date_to:
        raise UsageError(f"--from {date_from} is after --to {date_to}")
    return date_from, date_to
