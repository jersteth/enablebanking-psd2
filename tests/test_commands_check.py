from contextlib import nullcontext

from dataclasses import replace

from conftest import BANKS, OTHER_IBAN, VALID_IBAN, FakeClient, cli_args, cmd_args, make_cfg, make_ctx
from enablebanking_psd2 import cli
from enablebanking_psd2.commands import check
from enablebanking_psd2.config import FIELDS, REQUIRED
from enablebanking_psd2.eb.errors import ApiAuthError
from enablebanking_psd2.eb.models import Aspsp
from enablebanking_psd2.flow import Step, select_psu_headers

TWO_HEADERS = ["Psu-Ip-Address", "Psu-User-Agent"]


def _check(tmp_path, client, cfg=None, *argv):
    ctx, out, _ = make_ctx(tmp_path, client, cfg)
    return check(ctx, cmd_args("check", *argv)), out.getvalue(), client


def test_match_case_insensitive(tmp_path):
    res, out, _ = _check(tmp_path, FakeClient(), make_cfg(tmp_path, "kbc"))
    assert res.ok and res.exit_code == 0
    assert "[✓] BANK" in out and "180 days" in out
    assert res.summary["bank"] == "KBC" and res.summary["account"] == "main"


def test_no_match_lists_candidates(tmp_path):
    res, out, _ = _check(tmp_path, FakeClient(), make_cfg(tmp_path, "KBC Bank"))
    assert not res.ok and res.failed_step is Step.BANK and res.exit_code == 1
    assert "candidates: KBC, KBC Brussels, Belfius" in out
    _, out, _ = _check(tmp_path, FakeClient(), make_cfg(tmp_path, "brussels"))
    assert "candidates: KBC Brussels" in out


def test_api_auth_error(tmp_path):
    err = ApiAuthError(401, "GET", "/aspsps", message="Invalid JWT")
    res, out, _ = _check(tmp_path, FakeClient(aspsps_exc=err))
    assert res.exit_code == 1 and "status=401" in out and "hint:" in out


def test_without_account_checks_credentials_only(tmp_path):
    two = make_cfg(tmp_path, accounts={"a": ("KBC", VALID_IBAN), "b": ("KBC", OTHER_IBAN)})
    res, out, _ = _check(tmp_path, FakeClient(), two)
    assert res.ok and "credentials ok, 3 banks in BE (no account selected)" in out
    res, out, _ = _check(tmp_path, FakeClient(), two, "--account", "b")
    assert res.ok and res.summary["account"] == "b"


def test_unknown_account_is_an_error_not_a_fallback(tmp_path):
    res, out, client = _check(tmp_path, FakeClient(), None, "--account", "typo")
    assert res.exit_code == 2 and "unknown account 'typo'" in out and client.calls == []


def test_select_psu_headers_all_or_none():
    ua_only = {"Psu-User-Agent": "x"}
    both = {**ua_only, "Psu-Ip-Address": "203.0.113.10"}
    required = ["psu-ip-address", "psu-user-agent"]
    assert select_psu_headers(ua_only, required) == ({}, ["psu-ip-address"])
    assert select_psu_headers(both, required) == (both, [])
    assert select_psu_headers(ua_only, []) == (ua_only, [])


def test_bank_step_drops_partial_psu_headers(tmp_path):
    client = FakeClient(aspsps=[Aspsp("Belfius", "BE", required_psu_headers=TWO_HEADERS)])
    res, out, _ = _check(tmp_path, client, make_cfg(tmp_path, "Belfius"))
    assert res.ok and client.psu_headers == {}
    assert "not configured: Psu-Ip-Address" in out and "psu_ip_address" in out


def test_bank_step_keeps_complete_psu_headers(tmp_path):
    client = FakeClient(aspsps=[Aspsp("Belfius", "BE", required_psu_headers=TWO_HEADERS)])
    client.psu_headers = {"Psu-User-Agent": "x", "Psu-Ip-Address": "203.0.113.10"}
    _check(tmp_path, client, make_cfg(tmp_path, "Belfius"))
    assert client.psu_headers == {"Psu-User-Agent": "x", "Psu-Ip-Address": "203.0.113.10"}


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
    assert "[✓] CONFIG" in err and "1 account(s)" in err and "[✓] BANK" in err


def test_cli_bad_key_is_config_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    bad = tmp_path / "bad.pem"
    bad.write_text("garbage")
    bad.chmod(0o600)
    assert cli.main(cli_args("check", bad)) == 2
    assert "cannot be loaded" in capsys.readouterr().err


def test_without_account_uses_the_configured_country(tmp_path):
    cfg = replace(make_cfg(tmp_path), accounts={}, country="NL")
    res, out, client = _check(tmp_path, FakeClient(), cfg)
    assert res.ok and "banks in NL" in out and ("aspsps", "NL") in client.calls
