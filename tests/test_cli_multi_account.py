"""Three accounts at two banks through the CLI with a mocked API: one consent per bank."""

import json

import httpx
import respx

from conftest import BASE, FOREIGN_IBAN, OTHER_IBAN, VALID_IBAN, PasteStdin, load_fixture, write_config
from psd2demo import cli

MAIN, SAVINGS, GIRO = VALID_IBAN, OTHER_IBAN, FOREIGN_IBAN


def _config(tmp_path, key_file):
    return write_config(tmp_path, key_file, {"main": ("KBC", MAIN), "savings": ("KBC", SAVINGS),
                                             "giro": ("Belfius", GIRO)}, top='default_account = "main"')


@respx.mock
def test_one_consent_per_bank(tmp_path, monkeypatch, key_file, capsys):
    monkeypatch.chdir(tmp_path)
    _config(tmp_path, key_file)
    respx.get(f"{BASE}/aspsps").respond(json=load_fixture("aspsps.json"))
    auth = respx.post(f"{BASE}/auth").respond(json=load_fixture("auth.json"))
    respx.post(f"{BASE}/sessions").mock(side_effect=[
        httpx.Response(200, json=load_fixture("session_create.json")),
        httpx.Response(200, json=load_fixture("session_create_belfius.json"))])
    respx.get(f"{BASE}/sessions/99999999-8888-7777-6666-555555555555").respond(json=load_fixture("session_get.json"))
    for uid in ("acc-uid-1", "acc-uid-2", "acc-uid-3"):
        respx.get(f"{BASE}/accounts/{uid}/transactions").respond(json=load_fixture("transactions_p2.json"))
    monkeypatch.setattr("webbrowser.open", lambda url: None)
    monkeypatch.setattr("sys.stdin", PasteStdin(auth))

    assert cli.main(["run"]) == 0                         # default_account main: KBC consent
    assert cli.main(["run", "--account", "savings"]) == 0  # same bank: reused, no new /auth
    assert auth.call_count == 1
    assert "reused session" in capsys.readouterr().err
    assert cli.main(["transactions", "--account", "giro", "--json"]) == 0  # Belfius: second consent
    assert auth.call_count == 2
    assert json.loads(capsys.readouterr().out)["summary"]["account"] == "giro"

    store = json.loads((tmp_path / ".psd2demo" / "session.json").read_text())
    assert sorted(r["bank_name"] for r in store["sessions"]) == ["Belfius", "KBC"]
    assert cli.main(["run", "--account", "nope"]) == 2
