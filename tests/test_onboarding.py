import argparse
import stat

from conftest import APP_ID, OTHER_IBAN, VALID_IBAN, FakeClient
from enablebanking_psd2 import cli, onboarding
from enablebanking_psd2.config import load_config
from enablebanking_psd2.eb.errors import ApiServerError
from enablebanking_psd2.onboarding import Prompter, render_toml, run


class Script:
    """Answers prompts in order and records the prompts and printed output."""

    def __init__(self, *answers):
        self.answers, self.asked, self.printed = list(answers), [], []

    def prompter(self) -> Prompter:
        def ask(prompt):
            self.asked.append(prompt)
            if not self.answers:
                raise EOFError
            answer = self.answers.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        return Prompter(ask_fn=ask, out_fn=self.printed.append)

    @property
    def output(self) -> str:
        return "\n".join(self.printed)


def _args(target, force=False):
    return argparse.Namespace(config=target, force=force)


def _run(script, target, key_file=None, *, client=None, force=False, check=None):
    client = client or FakeClient()
    codes = []
    code = run(_args(target, force), {}, script.prompter(), client_factory=lambda cfg: client,
               check_fn=check or (lambda path: codes.append(path) or 0))
    return code, client, codes


def happy(key_file, *extra):
    # app id, key, redirect (default), psu ip, country (default), bank search "kbc" -> 2nd match... then iban, name
    return Script(APP_ID, str(key_file), "", "203.0.113.10", "", "kbc", "1", VALID_IBAN.lower(), "", "n", *extra)


def test_happy_path_writes_a_loadable_config_and_runs_check(tmp_path, key_file):
    target = tmp_path / "cfg" / "config.toml"
    script = happy(key_file)
    code, client, checked = _run(script, target)
    assert code == 0 and checked == [target]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600 and stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    cfg = load_config(target, {}, {}, cwd=tmp_path)
    assert cfg.app_id == APP_ID and cfg.key_path == key_file and cfg.psu_ip_address == "203.0.113.10"
    assert cfg.country == "BE" and cfg.redirect_url == "https://localhost/callback"
    assert cfg.default_account == "main"
    acc = cfg.accounts["main"]
    assert (acc.bank_name, acc.bank_country, acc.iban) == ("KBC", "BE", VALID_IBAN)
    assert ("aspsps", "BE") in client.calls
    assert "enablebanking authorize" in script.output
    assert "BEGIN" not in script.output and "PRIVATE" not in script.output  # the key is never echoed


def test_two_accounts_and_country(tmp_path, key_file):
    from enablebanking_psd2.eb.models import Aspsp
    nl = FakeClient(aspsps=[Aspsp("ING", "NL"), Aspsp("ABN AMRO", "NL")])
    script = Script(APP_ID, str(key_file), "https://example.com/cb", "", "nl", "ing", "1", "NL91ABNA0417164300", "main",
                    "y", "abn", "1", "NL86INGB0002445588", "main", "savings", "n")
    target = tmp_path / "config.toml"
    code, client, _ = _run(script, target, client=nl)
    assert code == 0 and ("aspsps", "NL") in client.calls and len([c for c in client.calls if c[0] == "aspsps"]) == 1
    assert "already used" in script.output
    cfg = load_config(target, {}, {}, cwd=tmp_path)
    assert cfg.country == "NL" and list(cfg.accounts) == ["main", "savings"]
    assert cfg.accounts["savings"].bank_name == "ABN AMRO" and cfg.psu_ip_address is None


def test_existing_file_declined_writes_nothing_and_force_overwrites(tmp_path, key_file):
    target = tmp_path / "config.toml"
    target.write_text("old")
    code, _, checked = _run(Script("n"), target)
    assert code == 1 and target.read_text() == "old" and checked == []
    code, _, _ = _run(happy(key_file), target, force=True)
    assert code == 0 and target.read_text().startswith("default_account")
    target.write_text("old")
    code, _, _ = _run(Script("y", *happy(key_file).answers), target)
    assert code == 0 and target.read_text().startswith("default_account")


def test_group_readable_key_is_rejected_with_the_chmod_hint(tmp_path, key_file):
    key_file.chmod(0o640)
    target = tmp_path / "config.toml"
    script = Script(APP_ID, str(key_file))
    code, _, _ = _run(script, target)
    assert code == 2 and f"chmod 600 {key_file}" in script.output and not target.exists()


def test_unreadable_pem_is_rejected(tmp_path):
    bad = tmp_path / "bad.pem"
    bad.write_text("not a key")
    bad.chmod(0o600)
    script = Script(APP_ID, str(bad))
    code, _, _ = _run(script, tmp_path / "config.toml")
    assert code == 2 and "cannot be loaded" in script.output and "not a key" not in script.output


def test_invalid_uuid_is_asked_again_then_gives_up(tmp_path, key_file):
    script = Script("nope", APP_ID, str(key_file), "", "", "", "kbc", "1", VALID_IBAN, "", "n")
    code, _, _ = _run(script, tmp_path / "config.toml")
    assert code == 0 and "not a UUID" in script.output
    code, _, _ = _run(Script("a", "b", "c"), tmp_path / "other.toml")
    assert code == 2 and not (tmp_path / "other.toml").exists()


def test_invalid_iban_is_asked_again(tmp_path, key_file):
    script = Script(APP_ID, str(key_file), "", "", "", "kbc", "1", "BE00 0000 0000 0000", OTHER_IBAN, "", "n")
    code, _, _ = _run(script, tmp_path / "config.toml")
    assert code == 0 and "invalid" in script.output
    assert load_config(tmp_path / "config.toml", {}, {}, cwd=tmp_path).accounts["main"].iban == OTHER_IBAN


def test_bank_search_without_match_and_bad_choice_repeat(tmp_path, key_file):
    script = Script(APP_ID, str(key_file), "", "", "", "zzz", "kbc", "9", "kbc", "1", VALID_IBAN, "", "n")
    code, _, _ = _run(script, tmp_path / "config.toml")
    assert code == 0 and "no bank matches" in script.output


def test_bank_list_failure_can_be_retried_or_quit(tmp_path, key_file):
    class Flaky(FakeClient):
        def get_aspsps(self, country, psu_type="personal"):
            if not getattr(self, "failed", False):
                self.failed = True
                raise ApiServerError(500, "GET", "/aspsps")
            return super().get_aspsps(country)

    script = Script(APP_ID, str(key_file), "", "", "", "y", "kbc", "1", VALID_IBAN, "", "n")
    code, _, _ = _run(script, tmp_path / "config.toml", client=Flaky())
    assert code == 0 and "could not list banks" in script.output
    script = Script(APP_ID, str(key_file), "", "", "", "n")
    code, _, _ = _run(script, tmp_path / "q.toml", client=FakeClient(aspsps_exc=ApiServerError(500, "GET", "/aspsps")))
    assert code == 1 and not (tmp_path / "q.toml").exists()


def test_ctrl_c_and_eof_write_nothing(tmp_path, key_file):
    target = tmp_path / "config.toml"
    code, _, _ = _run(Script(APP_ID, str(key_file), "", "", "", "kbc", "1", VALID_IBAN, KeyboardInterrupt()), target)
    assert code == 130 and not target.exists()
    code, _, _ = _run(Script(APP_ID), target)
    assert code == 1 and not target.exists()


def test_toml_escaping_round_trips(tmp_path, key_file):
    odd = tmp_path / 'we"ird\\dir'
    odd.mkdir()
    pem = odd / "k.pem"
    pem.write_bytes(key_file.read_bytes())
    pem.chmod(0o600)
    answers = onboarding.Answers(app_id=APP_ID, key_path=pem, country="BE",
                                 accounts=[{"name": "main", "bank_name": 'Bank "X" \\ Y', "bank_country": "BE",
                                            "iban": VALID_IBAN}])
    target = tmp_path / "config.toml"
    target.write_text(render_toml(answers))
    cfg = load_config(target, {}, {}, cwd=tmp_path)
    assert cfg.key_path == pem and cfg.accounts["main"].bank_name == 'Bank "X" \\ Y'
    assert render_toml(answers).index("default_account") < render_toml(answers).index("[enablebanking]")


def test_cli_init_runs_the_wizard_and_check(tmp_path, key_file, monkeypatch, capsys):
    target = tmp_path / "config.toml"
    script = happy(key_file)
    ask = script.prompter().ask_fn
    monkeypatch.setattr("builtins.input", ask)
    monkeypatch.setattr(onboarding, "make_client", lambda cfg, signer, **kw: FakeClient())
    seen = []
    monkeypatch.setattr(cli, "main", lambda argv=None: seen.append(argv) or 0)  # `check` is covered elsewhere
    args = cli.parse_args(["init", "--config", str(target)])
    assert args.cmd == "init" and args.config == target and not args.force
    assert onboarding.run(args, {}, check_fn=None) == 0
    assert seen == [["check", "--config", str(target)]] and target.exists()
