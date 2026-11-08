"""Configuration loading: CLI flags > environment > TOML file > defaults."""

import logging
import os
import tomllib
import uuid
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Literal, Mapping, Sequence
from urllib.parse import urlparse

from enablebanking_psd2 import iban as iban_mod
from enablebanking_psd2 import paths
from enablebanking_psd2.errors import ConfigError

log = logging.getLogger(__name__)

CaptureMode = Literal["paste", "listener"]
LOOPBACK_HOSTS = {"localhost", "127.0.0.1"}

# App-level keys: key -> (TOML section, env var). The [demo] section is kept for 002 compatibility.
FIELDS: dict[str, tuple[str, str]] = {
    "app_id": ("enablebanking", "EB_APP_ID"),
    "key_path": ("enablebanking", "EB_KEY_PATH"),
    "redirect_url": ("enablebanking", "EB_REDIRECT_URL"),
    "psu_ip_address": ("enablebanking", "EB_PSU_IP"),
    "country": ("enablebanking", "EB_COUNTRY"),
    "capture_mode": ("demo", "EB_CAPTURE_MODE"),
    "consent_days": ("demo", "EB_CONSENT_DAYS"),
    "callback_timeout": ("demo", "EB_CALLBACK_TIMEOUT"),
}
REQUIRED = ("app_id", "key_path", "redirect_url")
# Account keys, accepted in [accounts.<name>] and the legacy [demo] section.
ACCOUNT_KEYS = ("bank_name", "bank_country", "iban")
ACCOUNT_ENV = {"bank_name": "EB_BANK_NAME", "bank_country": "EB_BANK_COUNTRY", "iban": "EB_IBAN"}
HISTORY_PARTS = frozenset({"balances", "details", "raw"})
LEGACY_ACCOUNT, ENV_ACCOUNT, CLI_ACCOUNT = "demo", "env", "cli"
NO_ACCOUNTS = "no accounts configured; run `enablebanking init` or add [accounts.<name>] tables to the config file"
INIT_HINT = "no config file found (looked for ./config.toml and the per-user config); run `enablebanking init`"


@dataclass(frozen=True)
class AccountSpec:
    name: str
    bank_name: str
    bank_country: str
    iban: str  # normalized and validated


@dataclass(frozen=True)
class HistorySettings:
    days: int = 30
    include: frozenset[str] = frozenset()
    max_age: int = 60  # minutes


@dataclass(frozen=True)
class Config:
    app_id: str
    key_path: Path
    redirect_url: str
    accounts: dict[str, AccountSpec] = field(default_factory=dict)  # config order
    default_account: str | None = None
    forced_account: str | None = None  # env/CLI ad-hoc account; overrides any selection
    history: HistorySettings = field(default_factory=HistorySettings)
    capture_mode: CaptureMode = "paste"
    consent_days: int = 1
    callback_timeout: int = 300
    psu_ip_address: str | None = None
    country: str = "BE"  # default country for `check` and `banks`
    state_dir: Path = field(default=Path(".psd2demo"))
    export_dir: Path = field(default=Path("exports"))
    config_path: Path | None = None

    def account(self, name: str | None = None, *, required: bool = True) -> AccountSpec | None:
        """The account a command works on: forced (env/CLI) > ``name`` > default_account > the only one.

        With ``required=False``, "nothing to select" (no accounts, or several and none chosen) returns None;
        an unknown name still raises.
        """
        chosen = self.forced_account or name or self.default_account
        if chosen:
            if chosen not in self.accounts:
                raise ConfigError([f"unknown account {chosen!r}; configured: {self._names()}"])
            return self.accounts[chosen]
        if len(self.accounts) == 1:
            return next(iter(self.accounts.values()))
        if not required:
            return None
        if not self.accounts:
            raise ConfigError(["no account configured: add an [accounts.<name>] section "
                               "(bank_name, iban) or pass --bank and --iban"])
        raise ConfigError([f"several accounts configured; pass --account (one of: {self._names()})"])

    def select(self, names: Sequence[str] | None = None) -> list[AccountSpec]:
        """Accounts for multi-account commands: ``names`` in the given order, or all in config order."""
        if not names:
            return list(self.accounts.values())
        unknown = [n for n in names if n not in self.accounts]
        if unknown:
            raise ConfigError([f"unknown account {unknown[0]!r}; configured: {self._names()}"])
        return [self.accounts[n] for n in names]

    def _names(self) -> str:
        return ", ".join(self.accounts) or "none"


# Config's field defaults, except capture_mode: "auto" is resolved to paste/listener while loading.
DEFAULTS = {**{f.name: f.default for f in fields(Config) if f.name in FIELDS and f.default is not MISSING},
            "capture_mode": "auto"}


def is_loopback_http(url: str) -> bool:
    p = urlparse(url)
    return p.scheme == "http" and p.hostname in LOOPBACK_HOSTS and p.port is not None


def _read_toml(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError([f"config file {path} not found"])
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError([f"config file {path} is not valid TOML: {exc}"])
    return data


def _account(name: str, raw: Mapping, problems: list[str]) -> AccountSpec | None:
    bank, iban = raw.get("bank_name"), iban_mod.normalize(str(raw.get("iban") or ""))
    if not bank or not iban:
        problems.append(f"account {name!r} needs both bank_name and iban")
        return None
    try:
        iban_mod.validate(iban)
    except ValueError as exc:
        problems.append(f"account {name!r}: {exc}")
        return None
    return AccountSpec(name, str(bank), str(raw.get("bank_country") or "BE").upper(), iban)


def _load_accounts(data: dict, env: Mapping[str, str], overrides: Mapping[str, object],
                   problems: list[str]) -> tuple[dict[str, AccountSpec], str | None]:
    """[accounts.*] → legacy [demo] → env account → CLI account. Returns (accounts, forced account name)."""
    accounts: dict[str, AccountSpec] = {}
    tables = data.get("accounts", {})
    if not isinstance(tables, dict):
        problems.append("[accounts] must contain [accounts.<name>] tables")
        tables = {}
    for name, raw in tables.items():
        if acc := _account(name, raw if isinstance(raw, dict) else {}, problems):
            accounts[name] = acc
    legacy = {k: v for k, v in data.get("demo", {}).items() if k in ACCOUNT_KEYS}
    if legacy and LEGACY_ACCOUNT not in accounts and (acc := _account(LEGACY_ACCOUNT, legacy, problems)):
        accounts[LEGACY_ACCOUNT] = acc

    seen: dict[str, str] = {}
    for acc in accounts.values():
        if acc.iban in seen:
            problems.append(f"accounts {seen[acc.iban]!r} and {acc.name!r} have the same IBAN")
        seen.setdefault(acc.iban, acc.name)

    # Env and CLI values form an ad-hoc account layered on [demo] (002 precedence: flag > env > file).
    env_given = {k: env[v] for k, v in ACCOUNT_ENV.items() if env.get(v)}
    cli_given = {k: overrides[k] for k in ACCOUNT_KEYS if overrides.get(k)}
    identifies = {"bank_name", "iban"}.intersection
    forced = CLI_ACCOUNT if identifies(cli_given) else ENV_ACCOUNT if identifies(env_given) else None
    if forced and (acc := _account(forced, {**legacy, **env_given, **cli_given}, problems)):
        accounts[forced] = acc
    else:
        forced = None
    return accounts, forced


def parse_include(value) -> tuple[frozenset[str], frozenset[str]]:
    """A list or comma-separated string of parts ("none" = no parts) → (known parts, unknown parts)."""
    parts = [p.strip() for p in value.split(",")] if isinstance(value, str) else list(value)
    parts = frozenset(p for p in parts if p and p != "none")
    return parts & HISTORY_PARTS, parts - HISTORY_PARTS


def _history(data: dict, env: Mapping[str, str], problems: list[str]) -> HistorySettings:
    raw = dict(data.get("history", {}))
    for key, var in (("days", "EB_HISTORY_DAYS"), ("include", "EB_HISTORY_INCLUDE"), ("max_age", "EB_HISTORY_MAX_AGE")):
        if env.get(var):
            raw[key] = env[var]
    defaults = HistorySettings()
    try:
        days = int(raw.get("days", defaults.days))
        max_age = int(raw.get("max_age", defaults.max_age))
        if days < 1 or max_age < 0:
            raise ValueError
    except (TypeError, ValueError):
        problems.append("[history] days must be >= 1 and max_age >= 0 (integers)")
        return defaults
    parts, unknown = parse_include(raw.get("include", []))
    if unknown:
        problems.append(f"[history] include: unknown part(s) {sorted(unknown)}; allowed: {sorted(HISTORY_PARTS)}")
    return HistorySettings(days, parts, max_age)


def load_config(path: Path | None, env: Mapping[str, str], overrides: Mapping[str, object], *,
                cwd: Path | None = None) -> Config:
    """``overrides`` holds CLI values: app-level FIELDS keys plus bank_name/bank_country/iban (ad-hoc account)."""
    cwd = cwd or Path.cwd()
    config_path = paths.config_file(path, env, cwd)
    data = _read_toml(config_path)
    values: dict[str, object] = dict(DEFAULTS)
    for key, (section, _) in FIELDS.items():
        if key in data.get(section, {}):
            values[key] = data[section][key]
    for key, (_, var) in FIELDS.items():
        if env.get(var):
            values[key] = env[var]
    values.update({k: v for k, v in overrides.items() if k in FIELDS and v is not None})

    problems: list[str] = []
    accounts, forced = _load_accounts(data, env, overrides, problems)
    history = _history(data, env, problems)
    default_account = data.get("default_account")
    if default_account is not None and default_account not in accounts:
        problems.append(f"default_account {default_account!r} is not a configured account")
    for key in REQUIRED:
        if not values.get(key):
            problems.append(f"{key} is required (TOML [{FIELDS[key][0]}] {key} or env {FIELDS[key][1]})")
    if problems and config_path is None:
        problems.append(INIT_HINT)
    country = str(values["country"]).upper()
    if not (len(country) == 2 and country.isalpha()):
        problems.append("country must be a 2-letter country code (e.g. BE)")
    base = config_path.parent if config_path else None
    path_cfg = data.get("paths", {})

    for key in ("consent_days", "callback_timeout"):
        try:
            values[key] = int(values[key])
            if values[key] < 1:
                raise ValueError
        except (TypeError, ValueError):
            problems.append(f"{key} must be a positive integer")

    if values.get("app_id"):
        try:
            uuid.UUID(str(values["app_id"]))
        except ValueError:
            log.warning("app_id %r does not look like a UUID", values["app_id"])

    key_path = Path(str(values["key_path"])).expanduser() if values.get("key_path") else None
    if key_path is not None:
        if not key_path.is_file():
            problems.append(f"private key {key_path} not found")
        elif not os.access(key_path, os.R_OK):
            problems.append(f"private key {key_path} is not readable")
        elif key_path.stat().st_mode & 0o077:
            log.warning("private key %s is readable by others; run: chmod 600 %s", key_path, key_path)

    redirect_url = str(values.get("redirect_url") or "")
    if redirect_url:
        p = urlparse(redirect_url)
        if p.scheme not in ("http", "https") or not p.netloc:
            problems.append("redirect_url must be an absolute http(s) URL")

    mode = str(values["capture_mode"]).lower()
    if mode == "auto":
        mode = "listener" if is_loopback_http(redirect_url) else "paste"
    elif mode == "listener" and redirect_url and not is_loopback_http(redirect_url):
        problems.append("capture_mode=listener requires redirect_url http://localhost:<port>/... "
                        "or http://127.0.0.1:<port>/...")
    elif mode not in ("paste", "listener"):
        problems.append("capture_mode must be auto, paste or listener")

    if problems:
        raise ConfigError(problems)

    return Config(
        app_id=str(values["app_id"]),
        key_path=key_path,
        redirect_url=redirect_url,
        accounts=accounts,
        default_account=default_account,
        forced_account=forced,
        history=history,
        capture_mode=mode,
        consent_days=values["consent_days"],
        callback_timeout=values["callback_timeout"],
        psu_ip_address=str(values["psu_ip_address"]) if values.get("psu_ip_address") else None,
        country=country,
        state_dir=paths.state_dir(path_cfg.get("state_dir"), env, cwd, base),
        export_dir=paths.export_dir(path_cfg.get("export_dir"), env, cwd, base),
        config_path=config_path,
    )
