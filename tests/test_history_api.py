import io

import pytest

from conftest import NOW, OTHER_IBAN, TODAY, VALID_IBAN, VALID_STATUS, FakeClient, session_record, write_config
from psd2demo.errors import ConfigError
from psd2demo.history import fetch_history
from psd2demo.session_store import SessionStore
from test_schema import validate


@pytest.fixture
def config(tmp_path, key_file, monkeypatch):
    monkeypatch.chdir(tmp_path)  # state_dir .psd2demo is relative to the working directory
    path = write_config(tmp_path, key_file, {"main": ("KBC", VALID_IBAN), "savings": ("KBC", OTHER_IBAN)},
                        extra='\n[history]\ndays = 20\ninclude = ["balances"]\n')
    SessionStore(tmp_path / ".psd2demo" / "session.json").upsert(
        session_record(valid_until="2027-05-07T10:00:00+00:00"))
    return path


def _fetch(config, **kw):
    return fetch_history(config, env={}, today=TODAY, now=NOW, **{"client": FakeClient(session_status=VALID_STATUS), **kw})


def test_returns_schema_valid_document_and_prints_nothing(config, capsys):
    doc = _fetch(config)
    validate(doc)
    assert capsys.readouterr().out == ""
    assert doc["range"] == {"date_from": "2026-10-19", "date_to": "2026-11-08"}  # [history] days = 20
    assert doc["include"] == ["balances"]  # [history] include
    assert [a["status"] for a in doc["accounts"]] == ["ok", "consent_required"]


def test_arguments_override_config(config):
    doc = _fetch(config, accounts=["main"], days=5, include="none")
    assert doc["range"]["date_from"] == "2026-11-03" and doc["include"] == [] and len(doc["accounts"]) == 1
    doc = _fetch(config, date_from=TODAY.replace(day=1), include=["raw"], use_cache=False)
    assert doc["range"]["date_from"] == "2026-11-01" and "raw" in doc["accounts"][0]["transactions"][0]


@pytest.mark.parametrize("kwargs,match", [
    ({"include": ["everything"]}, "unknown include part"),
    ({"days": 5, "date_from": TODAY}, "either --days"),
    ({"accounts": ["ghost"]}, "unknown account 'ghost'"),
])
def test_only_config_errors_escape(config, kwargs, match):
    with pytest.raises(ConfigError, match=match):
        _fetch(config, **kwargs)


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        fetch_history(tmp_path / "nope.toml", env={})


def test_fully_cached_run_creates_no_client(config):
    _fetch(config, accounts=["main"])
    made = []
    doc = _fetch(config, accounts=["main"], client=lambda: made.append(1) or FakeClient())
    assert made == [] and doc["accounts"][0]["cached"]


def test_progress_lines_only_when_asked(config, capsys):
    stream = io.StringIO()
    _fetch(config, progress=stream, use_cache=False)
    assert "main: ok" in stream.getvalue() and "savings: consent_required" in stream.getvalue()
    assert capsys.readouterr() == ("", "")
