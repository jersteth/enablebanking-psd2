from datetime import datetime, timedelta, timezone

import pytest

from psd2demo.consent import compute_valid_until, new_state, parse_callback
from psd2demo.errors import ConsentDenied, ConsentError, StateMismatch

NOW = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)


def test_state_is_random_and_long():
    a, b = new_state(), new_state()
    assert a != b and len(a) >= 43


def test_valid_until_clamped():
    assert compute_valid_until(NOW, 1, 15552000) == NOW + timedelta(days=1, seconds=-60)
    assert compute_valid_until(NOW, 365, 7776000) == NOW + timedelta(days=90, seconds=-60)
    assert compute_valid_until(NOW, 2, None) == NOW + timedelta(days=2, seconds=-60)


def test_parse_ok():
    assert parse_callback(" https://localhost/callback?code=abc&state=s1 \n", "s1") == "abc"


def test_parse_error():
    with pytest.raises(ConsentDenied, match="access_denied.*user cancelled"):
        parse_callback("https://x/cb?error=access_denied&error_description=user+cancelled&state=s1", "s1")


@pytest.mark.parametrize("url", ["https://x/cb?code=abc&state=other", "https://x/cb?code=abc"])
def test_state_mismatch(url):
    with pytest.raises(StateMismatch):
        parse_callback(url, "s1")


def test_missing_code():
    with pytest.raises(ConsentError, match="no authorization code"):
        parse_callback("https://x/cb?state=s1", "s1")
