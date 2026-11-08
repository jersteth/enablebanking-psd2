import pytest

import enablebanking_psd2
from conftest import NOW, OTHER_IBAN, VALID_IBAN, FakeClient, load_fixture, session_record, write_config
from enablebanking_psd2 import api, history
from enablebanking_psd2.errors import ConfigError
from enablebanking_psd2.eb.models import SessionStatus
from enablebanking_psd2.session_store import SessionStore

LONG = SessionStatus.from_json({**load_fixture("session_get.json"), "access": {"valid_until": "2099-01-01T00:00:00Z"}})


@pytest.fixture
def config(tmp_path, key_file, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = write_config(tmp_path, key_file, {"main": ("KBC", VALID_IBAN), "giro": ("Belfius", OTHER_IBAN)})
    SessionStore(tmp_path / ".psd2demo" / "session.json").upsert(
        session_record(valid_until="2099-01-01T00:00:00+00:00"))
    return path


def test_public_names_are_the_same_objects():
    assert enablebanking_psd2.fetch_history is api.fetch_history is history.fetch_history
    assert enablebanking_psd2.consent_status is api.consent_status


def test_rows_per_account(config, capsys):
    client = FakeClient(session_status=LONG)
    result = api.consent_status(config, env={}, client=client, now=NOW)
    rows = {r["account"]: r for r in result["accounts"]}
    assert rows["main"]["status"] == "authorized" and rows["main"]["valid_until"] == "2099-01-01T00:00:00+00:00"
    assert rows["main"]["iban_masked"] == "BE68 **** **** 7034"
    assert rows["giro"]["status"] == "consent_required" and rows["giro"]["valid_until"] is None
    assert [c[0] for c in client.calls] == ["get_session"]  # one session check, none for the bank without a record
    assert capsys.readouterr().out == ""


def test_account_selection_and_unknown_account(config):
    only = api.consent_status(config, ["giro"], env={}, client=FakeClient(), now=NOW)
    assert [r["account"] for r in only["accounts"]] == ["giro"]
    with pytest.raises(ConfigError, match="unknown account"):
        api.consent_status(config, ["nope"], env={}, client=FakeClient(), now=NOW)


def test_no_client_is_created_without_a_stored_session(tmp_path, key_file, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = write_config(tmp_path, key_file, {"main": ("KBC", VALID_IBAN)})

    def boom(*a, **k):
        raise AssertionError("a client was created")

    monkeypatch.setattr(api, "make_client", boom)
    monkeypatch.setattr(api, "load_signer", boom)
    result = api.consent_status(path, env={}, now=NOW)
    assert result["accounts"][0]["status"] == "consent_required"


def test_a_client_is_created_lazily_and_closed_when_needed(config, monkeypatch):
    client = FakeClient(session_status=LONG)
    closed = []
    client.close = lambda: closed.append(True)
    monkeypatch.setattr(api, "load_signer", lambda cfg: None)
    monkeypatch.setattr(api, "make_client", lambda cfg, signer, **kw: client)
    assert api.consent_status(config, env={}, now=NOW)["accounts"][0]["status"] == "authorized"
    assert closed == [True]


def test_config_errors_are_the_only_errors(tmp_path):
    with pytest.raises(ConfigError):
        api.consent_status(tmp_path / "missing.toml", env={})
