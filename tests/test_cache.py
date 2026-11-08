import os
import stat
from datetime import date, datetime, timedelta, timezone

from enablebanking_psd2.cache import HistoryCache

NOW = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
KEY = HistoryCache.key("app", "hash", date(2026, 10, 9), date(2026, 11, 8), frozenset({"raw", "balances"}))


def test_key_is_stable_and_specific():
    assert KEY == HistoryCache.key("app", "hash", date(2026, 10, 9), date(2026, 11, 8), frozenset({"balances", "raw"}))
    assert KEY != HistoryCache.key("app", "hash", date(2026, 10, 9), date(2026, 11, 8), frozenset())
    assert KEY != HistoryCache.key("app", "other", date(2026, 10, 9), date(2026, 11, 8), frozenset({"raw", "balances"}))
    assert len(KEY) == 64


def test_put_get_and_max_age(tmp_path):
    cache = HistoryCache(tmp_path / "cache")
    assert cache.get(KEY, 60, NOW) is None
    cache.put(KEY, {"name": "main"}, NOW)
    assert cache.get(KEY, 60, NOW + timedelta(minutes=59)) == {"name": "main"}
    assert cache.get(KEY, 60, NOW + timedelta(minutes=61)) is None
    assert cache.get(KEY, None, NOW + timedelta(days=30)) == {"name": "main"}  # any age (stale fallback)
    path = tmp_path / "cache" / f"{KEY}.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_unreadable_is_a_miss(tmp_path, caplog):
    cache = HistoryCache(tmp_path)
    for content in ("{nope", "[]", '{"cache_version": 9}', '{"cache_version": 1}'):
        (tmp_path / f"{KEY}.json").write_text(content)
        assert cache.get(KEY, None, NOW) is None
    assert "ignoring unreadable cache file" in caplog.text


def test_prune(tmp_path):
    cache = HistoryCache(tmp_path / "cache")
    assert cache.prune(timedelta(days=7), NOW) == 0  # no directory yet
    cache.put("old", {}, NOW)
    cache.put("new", {}, NOW)
    eight_days_ago = (NOW - timedelta(days=8)).timestamp()
    os.utime(tmp_path / "cache" / "old.json", (eight_days_ago, eight_days_ago))
    assert cache.prune(timedelta(days=7), NOW) == 1
    assert [p.stem for p in (tmp_path / "cache").iterdir()] == ["new"]
