from pathlib import Path

import pytest

from conftest import APP_ID, VALID_IBAN
from enablebanking_psd2.config import load_config
from enablebanking_psd2.errors import ConfigError

OTHER_IBAN = "BE71096123456769"


def _toml(tmp_path, key_file, body, top=""):
    p = tmp_path / "config.toml"
    p.write_text(f'''{top}
[enablebanking]
app_id = "{APP_ID}"
key_path = "{key_file}"
redirect_url = "https://example.com/callback"
{body}
''')
    return p


@pytest.fixture
def toml_file(tmp_path, key_file):
    return _toml(tmp_path, key_file, '''
[demo]
bank_name = "KBC"
iban = "be68 5390 0754 7034"
''')


@pytest.fixture
def multi_toml(tmp_path, key_file):
    return _toml(tmp_path, key_file, f'''
[accounts.main]
bank_name = "KBC"
iban = "{VALID_IBAN}"

[accounts.savings]
bank_name = "Belfius"
bank_country = "be"
iban = "{OTHER_IBAN}"

[history]
days = 45
include = ["balances", "details"]
max_age = 15
''', top='default_account = "savings"')


def test_legacy_demo_section(toml_file):
    cfg = load_config(toml_file, {}, {})
    acc = cfg.account()
    assert (acc.name, acc.bank_name, acc.bank_country, acc.iban) == ("demo", "KBC", "BE", VALID_IBAN)
    assert cfg.capture_mode == "paste" and cfg.consent_days == 1
    assert cfg.history.days == 30 and cfg.history.include == frozenset() and cfg.history.max_age == 60


def test_precedence_flag_over_env_over_file(toml_file):
    cfg = load_config(toml_file, {"EB_BANK_NAME": "Belfius", "EB_CONSENT_DAYS": "3"}, {})
    assert cfg.account().bank_name == "Belfius" and cfg.account().iban == VALID_IBAN
    assert cfg.consent_days == 3
    cfg = load_config(toml_file, {"EB_BANK_NAME": "Belfius"}, {"bank_name": "ING"})
    assert cfg.account().bank_name == "ING" and cfg.forced_account == "cli"


def test_named_accounts_order_default_and_history(multi_toml):
    cfg = load_config(multi_toml, {}, {})
    assert list(cfg.accounts) == ["main", "savings"]
    assert cfg.account().name == "savings"  # default_account
    assert cfg.account("main").bank_name == "KBC"
    assert cfg.accounts["savings"].bank_country == "BE"
    assert [a.name for a in cfg.select(None)] == ["main", "savings"]
    assert [a.name for a in cfg.select(["savings", "main"])] == ["savings", "main"]
    assert cfg.history.days == 45 and cfg.history.include == {"balances", "details"} and cfg.history.max_age == 15


def test_unknown_account(multi_toml):
    cfg = load_config(multi_toml, {}, {})
    with pytest.raises(ConfigError, match="unknown account 'nope'.*main, savings"):
        cfg.account("nope")
    with pytest.raises(ConfigError, match="unknown account"):
        cfg.select(["main", "nope"])


def test_several_accounts_need_selection(tmp_path, key_file):
    path = _toml(tmp_path, key_file, f'''
[accounts.a]
bank_name = "KBC"
iban = "{VALID_IBAN}"
[accounts.b]
bank_name = "KBC"
iban = "{OTHER_IBAN}"
''')
    with pytest.raises(ConfigError, match="several accounts configured; pass --account"):
        load_config(path, {}, {}).account()


def test_cli_account_overrides_selection(multi_toml):
    cfg = load_config(multi_toml, {}, {"bank_name": "ING", "iban": "NL91ABNA0417164300"})
    assert cfg.account("main").name == "cli" and cfg.account().bank_name == "ING"


def test_half_specified_cli_account_without_demo_is_error(multi_toml):
    with pytest.raises(ConfigError, match="'cli' needs both bank_name and iban"):
        load_config(multi_toml, {}, {"bank_name": "ING"})


def test_duplicate_iban(tmp_path, key_file):
    path = _toml(tmp_path, key_file, f'''
[accounts.a]
bank_name = "KBC"
iban = "{VALID_IBAN}"
[accounts.b]
bank_name = "Belfius"
iban = "{VALID_IBAN}"
''')
    with pytest.raises(ConfigError, match="same IBAN"):
        load_config(path, {}, {})


def test_bad_history_and_default_account(tmp_path, key_file):
    path = _toml(tmp_path, key_file, top='default_account = "ghost"', body=f'''
[accounts.a]
bank_name = "KBC"
iban = "{VALID_IBAN}"
[history]
include = ["balances", "everything"]
''')
    with pytest.raises(ConfigError) as exc:
        load_config(path, {}, {})
    assert any("default_account 'ghost'" in p for p in exc.value.problems)
    assert any("unknown part(s) ['everything']" in p for p in exc.value.problems)
    with pytest.raises(ConfigError, match="days must be"):
        load_config(path.parent / "config.toml", {"EB_HISTORY_DAYS": "0"}, {})


def test_history_env(multi_toml):
    cfg = load_config(multi_toml, {"EB_HISTORY_INCLUDE": "raw, balances", "EB_HISTORY_DAYS": "7"}, {})
    assert cfg.history.include == {"raw", "balances"} and cfg.history.days == 7


def test_no_account_is_fine_until_needed(tmp_path, key_file):
    cfg = load_config(_toml(tmp_path, key_file, ""), {}, {})
    assert cfg.accounts == {}
    with pytest.raises(ConfigError, match="no account configured"):
        cfg.account()


def test_errors_are_aggregated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # don't pick up a developer's ./config.toml
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path / "missing.toml", {}, {})
    assert "not found" in str(exc.value)
    with pytest.raises(ConfigError) as exc:
        load_config(None, {"EB_KEY_PATH": str(tmp_path / "nokey.pem"), "EB_BANK_NAME": "KBC", "EB_IBAN": "BE00"}, {})
    problems = exc.value.problems
    assert any("app_id" in p for p in problems)
    assert any("private key" in p for p in problems)
    assert any("IBAN" in p for p in problems)
    assert any("redirect_url" in p for p in problems)


def test_capture_auto_listener(toml_file):
    cfg = load_config(toml_file, {}, {"redirect_url": "http://localhost:8765/callback"})
    assert cfg.capture_mode == "listener"


def test_listener_requires_loopback(toml_file):
    with pytest.raises(ConfigError, match="listener"):
        load_config(toml_file, {}, {"capture_mode": "listener"})


def test_loose_key_permissions_warn(toml_file, key_file, caplog):
    key_file.chmod(0o644)
    load_config(toml_file, {}, {})
    assert "chmod 600" in caplog.text


def test_bad_consent_days(toml_file):
    with pytest.raises(ConfigError, match="consent_days"):
        load_config(toml_file, {"EB_CONSENT_DAYS": "zero"}, {})


# --- location independence and country (005) ----------------------------------------------------------

ENV_ONLY = {"EB_APP_ID": APP_ID, "EB_REDIRECT_URL": "https://example.com/callback", "EB_BANK_NAME": "KBC",
            "EB_IBAN": VALID_IBAN}


def test_env_only_config_without_any_file(tmp_path, key_file):
    cfg = load_config(None, {**ENV_ONLY, "EB_KEY_PATH": str(key_file)}, {}, cwd=tmp_path)
    assert cfg.config_path is None and cfg.app_id == APP_ID and cfg.country == "BE"
    assert cfg.forced_account == "env" and cfg.accounts["env"].iban == VALID_IBAN


def test_no_config_and_incomplete_env_points_to_init(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load_config(None, {}, {}, cwd=tmp_path)
    assert any("app_id is required" in p for p in exc.value.problems)
    assert any("enablebanking init" in p for p in exc.value.problems)


def test_explicit_or_env_named_missing_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml", {}, {}, cwd=tmp_path)
    with pytest.raises(ConfigError, match="not found"):
        load_config(None, {"ENABLEBANKING_PSD2_CONFIG": str(tmp_path / "nope.toml")}, {}, cwd=tmp_path)


def test_user_config_is_found_from_any_directory(tmp_path, key_file, toml_file, monkeypatch):
    home = tmp_path / "home"
    (home / ".config" / "enablebanking-psd2").mkdir(parents=True)
    (home / ".config" / "enablebanking-psd2" / "config.toml").write_text(toml_file.read_text())
    monkeypatch.setenv("HOME", str(home))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    cfg = load_config(None, {}, {}, cwd=elsewhere)
    assert cfg.config_path == home / ".config" / "enablebanking-psd2" / "config.toml"
    assert cfg.state_dir == home / ".local" / "state" / "enablebanking-psd2"
    assert cfg.export_dir == home / ".local" / "share" / "enablebanking-psd2" / "exports"


def test_country_and_paths_section(tmp_path, key_file):
    toml = _toml(tmp_path, key_file, '''country = "nl"
[paths]
state_dir = "st"
export_dir = "/abs/out"
[demo]
bank_name = "ING"
bank_country = "NL"
iban = "NL91ABNA0417164300"
''')
    cfg = load_config(toml, {}, {}, cwd=tmp_path / "elsewhere")
    assert cfg.country == "NL"
    assert cfg.state_dir == tmp_path / "st" and cfg.export_dir == Path("/abs/out")  # relative → config file dir
    assert load_config(toml, {"EB_COUNTRY": "de"}, {}, cwd=tmp_path).country == "DE"


@pytest.mark.parametrize("value", ["B", "BEL", "1A"])
def test_invalid_country(tmp_path, key_file, value):
    with pytest.raises(ConfigError, match="country must be a 2-letter"):
        load_config(_toml(tmp_path, key_file, f'''country = "{value}"
[demo]
bank_name = "KBC"
iban = "{VALID_IBAN}"
'''), {}, {}, cwd=tmp_path)
