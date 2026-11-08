import argparse
from datetime import date

import pytest

from enablebanking_psd2.daterange import UsageError, iso_date, resolve_range

TODAY = date(2026, 11, 8)


def test_default_days():
    assert resolve_range(TODAY, default_days=10) == (date(2026, 10, 29), TODAY)


def test_days():
    assert resolve_range(TODAY, days=30, default_days=10) == (date(2026, 10, 9), TODAY)


def test_from_to_and_defaults():
    assert resolve_range(TODAY, date_from=date(2026, 10, 1), date_to=date(2026, 10, 15),
                         default_days=10) == (date(2026, 10, 1), date(2026, 10, 15))
    assert resolve_range(TODAY, date_from=date(2026, 10, 1), default_days=10) == (date(2026, 10, 1), TODAY)
    assert resolve_range(TODAY, date_to=date(2026, 10, 15), default_days=5) == (date(2026, 10, 10), date(2026, 10, 15))


@pytest.mark.parametrize("kwargs,match", [
    ({"days": 5, "date_from": date(2026, 10, 1)}, "either --days"),
    ({"days": 0}, "at least 1"),
    ({"date_to": date(2026, 11, 9)}, "future"),
    ({"date_from": date(2026, 10, 20), "date_to": date(2026, 10, 10)}, "after"),
])
def test_errors(kwargs, match):
    with pytest.raises(UsageError, match=match):
        resolve_range(TODAY, default_days=10, **kwargs)


def test_iso_date():
    assert iso_date("2026-10-01") == date(2026, 10, 1)
    with pytest.raises(argparse.ArgumentTypeError, match="YYYY-MM-DD"):
        iso_date("01/10/2026")
