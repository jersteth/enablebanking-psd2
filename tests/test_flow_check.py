import io
from contextlib import nullcontext

from conftest import BANKS, FakeClient, cli_args, make_cfg
from psd2demo import cli
from psd2demo.config import FIELDS, REQUIRED
from psd2demo.eb.errors import ApiAuthError
from psd2demo.flow import Step, StepReporter, run_check, select_psu_headers
from psd2demo.eb.models import Aspsp


def test_match_case_insensitive(tmp_path):
    out = io.StringIO()
    res = run_check(make_cfg(tmp_path, "kbc"), FakeClient(), StepReporter(out))
    assert res.ok and res.exit_code == 0
    assert "[✓] BANK" in out.getvalue() and "180 days" in out.getvalue()


def test_no_match_lists_candidates(tmp_path):
    out = io.StringIO()
    res = run_check(make_cfg(tmp_path, "KBC Bank"), FakeClient(), StepReporter(out))
    assert not res.ok and res.failed_step is Step.BANK and res.exit_code == 1
    assert "candidates: KBC, KBC Brussels, Belfius" in out.getvalue()
    out = io.StringIO()
    run_check(make_cfg(tmp_path, "brussels"), FakeClient(), StepReporter(out))
    assert "candidates: KBC Brussels" in out.getvalue()


def test_api_auth_error(tmp_path):
    out = io.StringIO()
    err = ApiAuthError(401, "GET", "/aspsps", message="Invalid JWT")
    res = run_check(make_cfg(tmp_path), FakeClient(exc=err), StepReporter(out))
    assert res.exit_code == 1
    assert "status=401" in out.getvalue() and "hint:" in out.getvalue()


def test_cli_config_error_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for key in REQUIRED:
        monkeypatch.delenv(FIELDS[key][1], raising=False)
    assert cli.main(["check"]) == 2
    assert "[✗] CONFIG" in capsys.readouterr().err


def test_cli_check_ok(tmp_path, monkeypatch, key_file, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_client", lambda cfg, signer: nullcontext(FakeClient(BANKS)))
    assert cli.main(cli_args("check", key_file)) == 0
    err = capsys.readouterr().err
    assert "[✓] CONFIG" in err and "BE68 **** **** 7034" in err and "[✓] BANK" in err


def test_cli_bad_key_is_config_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    bad = tmp_path / "bad.pem"
    bad.write_text("garbage")
    bad.chmod(0o600)
    assert cli.main(cli_args("check", bad)) == 2
    assert "cannot be loaded" in capsys.readouterr().err


def test_select_psu_headers_all_or_none():

    ua_only = {"Psu-User-Agent": "x"}
    both = {**ua_only, "Psu-Ip-Address": "203.0.113.10"}
    required = ["psu-ip-address", "psu-user-agent"]
    assert select_psu_headers(ua_only, required) == ({}, ["psu-ip-address"])
    assert select_psu_headers(both, required) == (both, [])
    assert select_psu_headers(ua_only, []) == (ua_only, [])


def test_bank_step_drops_partial_psu_headers(tmp_path):

    out = io.StringIO()
    client = FakeClient(aspsps=[Aspsp("Belfius", "BE", required_psu_headers=["Psu-Ip-Address", "Psu-User-Agent"])])
    res = run_check(make_cfg(tmp_path, "Belfius"), client, StepReporter(out))
    assert res.ok and client.psu_headers == {}
    assert "not configured: Psu-Ip-Address" in out.getvalue() and "psu_ip_address" in out.getvalue()


def test_bank_step_keeps_complete_psu_headers(tmp_path):

    client = FakeClient(aspsps=[Aspsp("Belfius", "BE", required_psu_headers=["Psu-Ip-Address", "Psu-User-Agent"])])
    client.psu_headers = {"Psu-User-Agent": "x", "Psu-Ip-Address": "203.0.113.10"}
    run_check(make_cfg(tmp_path, "Belfius"), client, StepReporter(io.StringIO()))
    assert client.psu_headers == {"Psu-User-Agent": "x", "Psu-Ip-Address": "203.0.113.10"}
