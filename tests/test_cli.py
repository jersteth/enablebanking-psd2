import json

import pytest
import respx

from conftest import BASE, cli_args, load_fixture
from psd2demo import cli
from psd2demo.cli import parse_args


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "run" in out and "check" in out


def test_no_subcommand_defaults_to_run():
    assert parse_args([]).command == "run"
    args = parse_args(["--fresh"])
    assert args.command == "run" and args.fresh


def test_check_subcommand_overrides():
    args = parse_args(["check", "--bank", "KBC", "--iban", "BE68 5390 0754 7034"])
    assert args.command == "check"
    assert args.bank_name == "KBC"


# --- end-to-end `run` through the CLI with a mocked Enable Banking API -------------------------------

@pytest.fixture
def api(monkeypatch, tmp_path):
    """Mock the API, the browser and the paste prompt (which answers with this run's state)."""
    monkeypatch.chdir(tmp_path)
    respx.get(f"{BASE}/aspsps").respond(json=load_fixture("aspsps.json"))
    auth = respx.post(f"{BASE}/auth").respond(json=load_fixture("auth.json"))
    respx.post(f"{BASE}/sessions").respond(json=load_fixture("session_create.json"))
    respx.get(f"{BASE}/accounts/acc-uid-1/transactions").respond(json=load_fixture("transactions_p2.json"))
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    monkeypatch.setattr("sys.stdin", _PasteStdin(auth))
    return opened


class _PasteStdin:
    """stdin whose next line is the redirect URL carrying the state of the /auth request just made."""

    def __init__(self, auth_route):
        self.auth_route = auth_route

    def readline(self):
        state = json.loads(self.auth_route.calls.last.request.content)["state"]
        return f"https://localhost/callback?code=C1&state={state}\n"


@respx.mock
def test_run_end_to_end(tmp_path, key_file, api, capsys):
    assert cli.main(cli_args("run", key_file)) == 0
    captured = capsys.readouterr()
    assert api and "[✓] TRANSACTIONS" in captured.err
    assert "US Shop" in captured.out and "Summary:" in captured.out
    assert (tmp_path / ".psd2demo" / "session.json").exists()


@respx.mock
def test_run_json_and_export(tmp_path, key_file, api, capsys):
    export = tmp_path / "exports" / "run.json"
    assert cli.main(cli_args("run", key_file, "--json", "--export", str(export))) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)  # stdout holds only the JSON document, not the paste prompt
    assert result["ok"] is True and result["failed_step"] is None
    assert "Paste the full redirect URL" in captured.err and "tilisy.enablebanking.com" in captured.err
    assert [s["name"] for s in result["steps"]] == ["CONFIG", "BANK", "CONSENT", "ACCOUNT", "TRANSACTIONS",
                                                    "EXPORT"]
    assert result["summary"]["totals"]["USD"]["net"] == "-15.00"
    assert "[✓]" not in captured.err
    assert json.loads(export.read_text())["raw_pages"][0]["transactions"][0]["entry_reference"] == "REF-004"


@respx.mock
def test_run_export_failure_keeps_output(tmp_path, key_file, api, capsys):
    (tmp_path / "blocker").write_text("")
    assert cli.main(cli_args("run", key_file, "--export", str(tmp_path / "blocker" / "run.json"))) == 1
    captured = capsys.readouterr()
    assert "[✗] EXPORT" in captured.err and "US Shop" in captured.out


@respx.mock
def test_check_json_failure(tmp_path, monkeypatch, key_file, capsys):
    monkeypatch.chdir(tmp_path)
    respx.get(f"{BASE}/aspsps").respond(403, json={"code": 403, "message": "Application does not exist"})
    result_code = cli.main(cli_args("check", key_file, "--json"))
    result = json.loads(capsys.readouterr().out)
    assert result_code == 1 and result["ok"] is False and result["failed_step"] == "BANK"
    assert result["steps"][-1]["detail"] == "Application does not exist"
