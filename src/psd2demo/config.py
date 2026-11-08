"""Configuration loading: CLI flags > environment > TOML file > defaults."""

import logging
import os
import tomllib
import uuid
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Literal, Mapping
from urllib.parse import urlparse

from psd2demo import iban as iban_mod
from psd2demo.errors import ConfigError

log = logging.getLogger(__name__)

CaptureMode = Literal["paste", "listener"]
LOOPBACK_HOSTS = {"localhost", "127.0.0.1"}

# key -> (TOML section, env var)
FIELDS: dict[str, tuple[str, str]] = {
    "app_id": ("enablebanking", "EB_APP_ID"),
    "key_path": ("enablebanking", "EB_KEY_PATH"),
    "redirect_url": ("enablebanking", "EB_REDIRECT_URL"),
    "psu_ip_address": ("enablebanking", "EB_PSU_IP"),
    "bank_name": ("demo", "EB_BANK_NAME"),
    "bank_country": ("demo", "EB_BANK_COUNTRY"),
    "iban": ("demo", "EB_IBAN"),
    "capture_mode": ("demo", "EB_CAPTURE_MODE"),
    "consent_days": ("demo", "EB_CONSENT_DAYS"),
    "callback_timeout": ("demo", "EB_CALLBACK_TIMEOUT"),
}
REQUIRED = ("app_id", "key_path", "redirect_url", "bank_name", "iban")


@dataclass(frozen=True)
class Config:
    app_id: str
    key_path: Path
    redirect_url: str
    bank_name: str
    iban: str
    bank_country: str = "BE"
    capture_mode: CaptureMode = "paste"
    consent_days: int = 1
    callback_timeout: int = 300
    psu_ip_address: str | None = None
    state_dir: Path = field(default=Path(".psd2demo"))


# Config's field defaults, except capture_mode: "auto" is resolved to paste/listener while loading.
DEFAULTS = {**{f.name: f.default for f in fields(Config) if f.name in FIELDS and f.default is not MISSING},
            "capture_mode": "auto"}


def is_loopback_http(url: str) -> bool:
    p = urlparse(url)
    return p.scheme == "http" and p.hostname in LOOPBACK_HOSTS and p.port is not None


def _read_toml(path: Path | None) -> dict:
    if path is None:
        default = Path("config.toml")
        if not default.exists():
            return {}
        path = default
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError([f"config file {path} not found"])
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError([f"config file {path} is not valid TOML: {exc}"])
    flat = {}
    for key, (section, _) in FIELDS.items():
        if key in data.get(section, {}):
            flat[key] = data[section][key]
    return flat


def load_config(path: Path | None, env: Mapping[str, str], overrides: Mapping[str, object]) -> Config:
    values: dict[str, object] = dict(DEFAULTS)
    values.update(_read_toml(path))
    for key, (_, var) in FIELDS.items():
        if env.get(var):
            values[key] = env[var]
    values.update({k: v for k, v in overrides.items() if v is not None})

    problems: list[str] = []
    for key in REQUIRED:
        if not values.get(key):
            problems.append(f"{key} is required (TOML [{FIELDS[key][0]}] {key} or env {FIELDS[key][1]})")

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

    iban = iban_mod.normalize(str(values.get("iban") or ""))
    if iban:
        try:
            iban_mod.validate(iban)
        except ValueError as exc:
            problems.append(str(exc))

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
        bank_name=str(values["bank_name"]),
        iban=iban,
        bank_country=str(values["bank_country"]).upper(),
        capture_mode=mode,
        consent_days=values["consent_days"],
        callback_timeout=values["callback_timeout"],
        psu_ip_address=str(values["psu_ip_address"]) if values.get("psu_ip_address") else None,
    )
