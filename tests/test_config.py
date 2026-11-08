import pytest

from conftest import APP_ID, VALID_IBAN
from psd2demo.config import load_config
from psd2demo.errors import ConfigError


@pytest.fixture
def toml_file(tmp_path, key_file):
    p = tmp_path / "config.toml"
    p.write_text(f'''
[enablebanking]
app_id = "{APP_ID}"
key_path = "{key_file}"
redirect_url = "https://example.com/callback"
[demo]
bank_name = "KBC"
iban = "be68 5390 0754 7034"
''')
    return p


def test_toml_defaults(toml_file):
    cfg = load_config(toml_file, {}, {})
    assert cfg.iban == VALID_IBAN
    assert cfg.bank_country == "BE"
    assert cfg.capture_mode == "paste"
    assert cfg.consent_days == 1


def test_precedence_flag_over_env_over_file(toml_file):
    cfg = load_config(toml_file, {"EB_BANK_NAME": "Belfius", "EB_CONSENT_DAYS": "3"}, {"bank_name": "ING"})
    assert cfg.bank_name == "ING"
    assert cfg.consent_days == 3


def test_errors_are_aggregated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # don't pick up a developer's ./config.toml
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path / "missing.toml", {}, {})
    assert "not found" in str(exc.value)
    with pytest.raises(ConfigError) as exc:
        load_config(None, {"EB_KEY_PATH": str(tmp_path / "nokey.pem"), "EB_IBAN": "BE00"}, {})
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
