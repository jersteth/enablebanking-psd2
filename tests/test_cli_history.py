"""`psd2-demo history` end to end through the CLI with a mocked Enable Banking API."""

import json
import stat

import pytest
import respx

from conftest import BASE, OTHER_IBAN, VALID_IBAN, load_fixture, session_record, write_config
from psd2demo import cli
from psd2demo.session_store import BankMeta, SessionStore
from test_schema import validate


@pytest.fixture
def setup(tmp_path, key_file, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_config(tmp_path, key_file, {"main": ("KBC", VALID_IBAN), "other": ("Belfius", OTHER_IBAN)})
    SessionStore(tmp_path / ".psd2demo" / "session.json").upsert(session_record(
        valid_until="2099-01-01T00:00:00+00:00", bank=BankMeta(15552000, ())))
    respx.get(f"{BASE}/sessions/old-session").respond(json={**load_fixture("session_get.json"),
                                                            "access": {"valid_until": "2099-01-01T00:00:00Z"}})
    tx = respx.get(f"{BASE}/accounts/acc-uid-1/transactions").respond(json=load_fixture("transactions_p2.json"))
    return tmp_path, tx


@respx.mock
def test_partial_run_exit_3_and_pure_json_stdout(setup, capsys):
    assert cli.main(["history", "--days", "30"]) == 3
    out = capsys.readouterr()
    doc = json.loads(out.out)  # stdout is the document and nothing else
    validate(doc)
    assert [a["status"] for a in doc["accounts"]] == ["ok", "consent_required"]
    assert out.err == ""  # quiet without --verbose


@respx.mock
def test_all_ok_exit_0_and_verbose_progress(setup, capsys):
    assert cli.main(["history", "--account", "main", "--verbose"]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["summary"]["accounts"]["ok"] == 1 and "main: ok" in out.err


@respx.mock
def test_none_ok_exit_1(setup, capsys):
    assert cli.main(["history", "--account", "other"]) == 1
    assert json.loads(capsys.readouterr().out)["accounts"][0]["status"] == "consent_required"


@respx.mock
def test_output_file_and_cache(setup, capsys):
    tmp_path, tx = setup
    target = tmp_path / "exports" / "h.json"
    assert cli.main(["history", "--account", "main", "--output", str(target)]) == 0
    assert capsys.readouterr().out == "" and stat.S_IMODE(target.stat().st_mode) == 0o600
    validate(json.loads(target.read_text()))
    calls = tx.call_count
    assert cli.main(["history", "--account", "main", "--output", str(target)]) == 0
    assert tx.call_count == calls and json.loads(target.read_text())["accounts"][0]["api_calls"] == 0
    assert cli.main(["history", "--account", "main", "--no-cache", "--output", str(target)]) == 0
    assert tx.call_count == calls + 1


@respx.mock
def test_output_outside_exports_warns_and_write_failure_exits_1(setup, capsys):
    tmp_path, _ = setup
    assert cli.main(["history", "--account", "main", "--output", str(tmp_path / "h.json")]) == 0
    assert "outside exports/" in capsys.readouterr().err
    (tmp_path / "blocker").write_text("")
    assert cli.main(["history", "--account", "main", "--output", str(tmp_path / "blocker" / "h.json")]) == 1
    out = capsys.readouterr()
    assert out.out == "" and "cannot write" in out.err


@pytest.mark.parametrize("argv,match", [
    (["--account", "ghost"], "unknown account 'ghost'"),
    (["--include", "everything"], "unknown include part"),
    (["--days", "3", "--to", "2026-01-01"], "either --days"),
])
def test_config_and_usage_errors_exit_2_without_document(setup, capsys, argv, match):
    assert cli.main(["history", *argv]) == 2
    out = capsys.readouterr()
    assert out.out == "" and match in out.err


def test_ctrl_c_exits_130(monkeypatch, capsys):
    def interrupted(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr("psd2demo.commands.fetch_history", interrupted)
    assert cli.main(["history"]) == 130


def test_parsing():
    args = cli.parse_args(["history", "--account", "a", "--account", "b", "--include", "balances,raw", "--json"])
    assert args.cmd == "history" and args.account == ["a", "b"] and args.include == "balances,raw"
    with pytest.raises(SystemExit):
        cli.parse_args(["history", "--days", "3", "--from", "2026-01-01"])
