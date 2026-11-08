import json

import pytest
import respx

from conftest import APP_ID, BASE, PasteStdin, cli_args, load_fixture
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
    monkeypatch.setattr("sys.stdin", PasteStdin(auth))
    return opened


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


def test_transactions_alias_and_period_flags():
    args = parse_args(["transactions", "--days", "30", "--account", "main"])
    assert args.cmd == "run" and args.days == 30 and args.account == "main"
    args = parse_args(["--from", "2026-10-01", "--to", "2026-10-15"])
    assert args.cmd == "run" and str(args.date_from) == "2026-10-01" and str(args.date_to) == "2026-10-15"


@pytest.mark.parametrize("argv", [["run", "--days", "5", "--from", "2026-10-01"], ["run", "--from", "01/10/2026"]])
def test_period_usage_errors_exit_2(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2


@respx.mock
def test_days_with_to_exit_2(tmp_path, key_file, api, capsys):
    assert cli.main(cli_args("run", key_file, "--days", "5", "--to", "2026-01-01")) == 2
    assert "either --days" in capsys.readouterr().err


def test_ctrl_c_exits_130(monkeypatch, capsys):
    def _execute(args, reporter):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_execute", _execute)
    assert cli.main(["check"]) == 130
    assert "interrupted" in capsys.readouterr().err




@pytest.mark.parametrize("command", ["run", "transactions", "check", "banks", "balances", "accounts", "sessions",
                                     "sessions revoke"])
def test_subcommand_help_and_dispatch(command, capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args([*command.split(), "--help"])
    assert exc.value.code == 0 and "Examples:" in capsys.readouterr().out
    assert parse_args(command.split()).cmd == {"transactions": "run"}.get(command, command.split()[0])


def test_banks_country_does_not_create_an_account():
    args = parse_args(["banks", "--country", "nl", "--filter", "ing"])
    assert args.country == "nl" and getattr(args, "bank_country", None) is None


@respx.mock
def test_banks_end_to_end_json(tmp_path, monkeypatch, key_file, capsys):
    monkeypatch.chdir(tmp_path)
    route = respx.get(f"{BASE}/aspsps").respond(json=load_fixture("aspsps.json"))
    assert cli.main(["banks", "--filter", "bel", "--json", "--app-id", APP_ID, "--key", str(key_file),
                     "--redirect-url", "https://localhost/callback"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [b["name"] for b in out["summary"]["banks"]] == ["Belfius"]
    assert route.calls.last.request.url.params["country"] == "BE"


def test_sessions_parsing():
    assert parse_args(["sessions"]).cmd == "sessions" and parse_args(["sessions"]).action is None
    args = parse_args(["sessions", "revoke", "--all", "--yes", "--json"])
    assert args.action == "revoke" and args.all and args.yes and args.json
    assert parse_args(["sessions", "list", "--offline"]).offline
    args = parse_args(["sessions", "--json", "--verbose", "revoke", "--all"])  # options before the action
    assert args.json and args.verbose and args.all
    with pytest.raises(SystemExit):
        parse_args(["sessions", "revoke", "--all", "--bank", "KBC"])


def test_authorize_parsing():
    args = parse_args(["authorize", "--account", "a", "--account", "b", "--consent-days", "30"])
    assert args.cmd == "authorize" and args.account == ["a", "b"] and args.consent_days == 30 and not args.status
    assert parse_args(["authorize", "--status"]).status
    with pytest.raises(SystemExit):
        parse_args(["authorize", "--all", "--account", "a"])
