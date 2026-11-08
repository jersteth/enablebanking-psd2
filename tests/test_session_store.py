import stat

from conftest import APP_ID, VALID_IBAN, stored_session as _rec
from psd2demo.session_store import SessionStore


def test_round_trip_and_permissions(tmp_path):
    store = SessionStore(tmp_path / "state" / "session.json")
    assert store.load() is None
    store.save(_rec())
    assert store.load() == _rec()
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
    assert [p.name for p in store.path.parent.iterdir()] == ["session.json"]
    assert VALID_IBAN not in store.path.read_text()
    store.clear()
    assert store.load() is None


def test_corrupt_file(tmp_path, caplog):
    store = SessionStore(tmp_path / "session.json")
    store.path.write_text("{nope")
    assert store.load() is None
    store.path.write_text('{"schema_version": 99}')
    assert store.load() is None
    assert "ignoring" in caplog.text


def test_matches():
    rec = _rec()
    assert rec.matches(APP_ID, "kbc", "BE", VALID_IBAN)
    assert not rec.matches(APP_ID, "KBC", "BE", "BE71096123456769")
    assert not rec.matches("other", "KBC", "BE", VALID_IBAN)
