import json
import stat
from datetime import datetime, timedelta, timezone

from conftest import APP_ID, VALID_IBAN, session_record
from enablebanking_psd2.config import AccountSpec
from enablebanking_psd2.session_store import BankMeta, SessionStore, StoredAccount, iban_hash

OTHER_IBAN = "BE71096123456769"
V1 = {"schema_version": 1, "session_id": "s1", "app_id": APP_ID, "bank_name": "KBC", "bank_country": "BE",
      "iban_sha256": iban_hash(VALID_IBAN), "account_uid": "u1", "valid_until": "2026-11-09T10:00:00+00:00",
      "created_at": "2026-11-08T10:00:00+00:00"}


def _store(tmp_path):
    return SessionStore(tmp_path / "state" / "session.json")


def test_round_trip_and_permissions(tmp_path):
    store = _store(tmp_path)
    assert store.records() == []
    rec = session_record(bank=BankMeta(15552000, ("Psu-Ip-Address",)))
    store.upsert(rec)
    assert store.records() == [rec]
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
    assert [p.name for p in store.path.parent.iterdir()] == ["session.json"]
    assert VALID_IBAN not in store.path.read_text()
    assert json.loads(store.path.read_text())["schema_version"] == 2


def test_upsert_replaces_same_bank_only(tmp_path):
    store = _store(tmp_path)
    store.upsert(session_record())
    store.upsert(session_record(session_id="belfius", bank_name="Belfius"))
    store.upsert(session_record(session_id="new-kbc", bank_name="kbc"))  # case-insensitive bank match
    assert sorted(r.session_id for r in store.records()) == ["belfius", "new-kbc"]


def test_lookups(tmp_path):
    store = _store(tmp_path)
    store.upsert(session_record(accounts=(StoredAccount("u1", iban_hash(VALID_IBAN)),
                                          StoredAccount("u2", iban_hash(OTHER_IBAN)))))
    assert store.for_bank(APP_ID, "kbc", "be").session_id == "old-session"
    assert store.for_bank("other-app", "KBC", "BE") is None
    rec = store.for_account(APP_ID, AccountSpec("s", "KBC", "BE", OTHER_IBAN))
    assert rec and rec.account_uid(OTHER_IBAN) == "u2"
    assert store.for_account(APP_ID, AccountSpec("x", "KBC", "BE", "NL91ABNA0417164300")) is None


def test_validity_and_revoke(tmp_path):
    store = _store(tmp_path)
    rec = session_record()
    now = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
    assert rec.valid_at(now, timedelta(minutes=5))
    assert not rec.valid_at(now, timedelta(days=2))
    store.upsert(rec)
    revoked = store.mark_revoked(rec)
    assert revoked.status == "revoked" and not revoked.valid_at(now)
    assert store.records() == [revoked]


def test_remove(tmp_path):
    store = _store(tmp_path)
    store.upsert(session_record())
    store.upsert(session_record(session_id="b", bank_name="Belfius", status="revoked"))
    assert store.remove(lambda r: r.status == "revoked") == 1
    assert [r.session_id for r in store.records()] == ["old-session"]
    assert store.remove(lambda r: False) == 0


def test_v1_migration_reads_without_writing(tmp_path):
    store = _store(tmp_path)
    store.path.parent.mkdir()
    store.path.write_text(json.dumps(V1))
    (rec,) = store.records()
    assert rec.account_uid(VALID_IBAN) == "u1" and rec.bank is None and rec.status == "active"
    assert json.loads(store.path.read_text())["schema_version"] == 1
    store.upsert(rec)
    assert json.loads(store.path.read_text())["schema_version"] == 2


def test_unreadable_file_is_backed_up_on_first_write(tmp_path, caplog):
    store = _store(tmp_path)
    store.path.parent.mkdir()
    for content in ("{nope", "[]", "null", '{"schema_version": 99}', '{"schema_version": 2, "sessions": [{}]}'):
        store.path.write_text(content)
        assert store.records() == []
    assert "ignoring" in caplog.text
    store.upsert(session_record())
    assert store.path.with_name("session.json.bak").read_text() == '{"schema_version": 2, "sessions": [{}]}'
    assert len(store.records()) == 1
