"""``enablebanking init``: a guided first-time setup that ends with a working config file.

Asks for the Enable Banking application (ID, private key, redirect URL), optionally your public IP, a country
and one or more accounts (found by bank search), then writes the config in one atomic step and runs ``check``.
Nothing is written before the end; the key's contents are never printed.
"""

from __future__ import annotations

import argparse
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from enablebanking_psd2 import iban as iban_mod
from enablebanking_psd2 import paths
from enablebanking_psd2.bootstrap import load_signer, make_client
from enablebanking_psd2.config import Config
from enablebanking_psd2.eb.errors import ApiError
from enablebanking_psd2.eb.models import Aspsp
from enablebanking_psd2.errors import ConfigError, DemoError
from enablebanking_psd2.fileio import write_text_atomic
from enablebanking_psd2.flow import EXIT_CONFIG, EXIT_FAIL, EXIT_INTERRUPT, EXIT_OK
from urllib.parse import urlparse

ATTEMPTS = 3
MAX_MATCHES = 20
ACCOUNT_NAME = re.compile(r"[a-z0-9_-]+")
DEFAULT_REDIRECT = "https://localhost/callback"


class _Abort(Exception):
    def __init__(self, code: int):
        self.code = code


@dataclass
class Prompter:
    """Injectable I/O (tests script it). ``ask`` and ``out`` are resolved when called, so pytest capture works."""
    ask_fn: Callable[[str], str] | None = None
    out_fn: Callable[[str], None] | None = None

    def ask(self, prompt: str) -> str:
        return (self.ask_fn or input)(prompt).strip()

    def out(self, text: str = "") -> None:
        (self.out_fn or print)(text)


@dataclass
class Answers:
    app_id: str = ""
    key_path: Path = Path()
    redirect_url: str = DEFAULT_REDIRECT
    psu_ip: str = ""
    country: str = "BE"
    accounts: list[dict] = field(default_factory=list)  # {"name", "bank_name", "bank_country", "iban"}


def _retry(p: Prompter, prompt: str, check: Callable[[str], str | None], default: str = "") -> str:
    """Ask until ``check`` (returns an error message or None) accepts the answer; ATTEMPTS tries, then exit 2."""
    for _ in range(ATTEMPTS):
        answer = p.ask(prompt) or default
        if (problem := check(answer)) is None:
            return answer
        p.out(f"  {problem}")
    p.out("Too many invalid answers; nothing was written.")
    raise _Abort(EXIT_CONFIG)


def _check_uuid(value: str) -> str | None:
    try:
        uuid.UUID(value)
    except ValueError:
        return "that is not a UUID; copy the application ID from the Enable Banking control panel"
    return None


def _check_url(value: str) -> str | None:
    u = urlparse(value)
    return None if u.scheme in ("http", "https") and u.netloc else "enter an absolute http(s) URL"


def _ask_key(p: Prompter, app_id: str) -> Path:
    def check(value: str) -> str | None:
        path = Path(value).expanduser()
        return None if path.is_file() else f"{path} is not a file"

    path = Path(_retry(p, "Path to the private key (.pem) downloaded at registration: ", check)).expanduser()
    if not paths._is_windows() and path.stat().st_mode & 0o077:
        p.out(f"The key {path} is readable by other users. Protect it first:\n  chmod 600 {path}\nthen run init again.")
        raise _Abort(EXIT_CONFIG)
    try:
        load_signer(Config(app_id=app_id, key_path=path, redirect_url=DEFAULT_REDIRECT))
    except ConfigError as exc:
        p.out(f"  {exc.message}")  # the message names the file and the parser's complaint, never the key itself
        raise _Abort(EXIT_CONFIG)
    return path


def _banks_for(p: Prompter, client, country: str, cache: dict[str, list[Aspsp]]) -> list[Aspsp]:
    while country not in cache:
        try:
            cache[country] = client.get_aspsps(country)
        except (ApiError, DemoError) as exc:
            p.out(f"  could not list banks: {exc.message}" + (f" ({exc.hint})" if exc.hint else ""))
            if p.ask("Retry? [Y/n] ").lower() in ("n", "no"):
                raise _Abort(EXIT_FAIL)
    return cache[country]


def _choose_bank(p: Prompter, client, country: str, cache: dict[str, list[Aspsp]]) -> Aspsp:
    banks = _banks_for(p, client, country, cache)
    while True:
        text = p.ask(f"Search your bank in {country} (part of its name): ").casefold()
        found = sorted((b for b in banks if text in b.name.casefold()), key=lambda b: b.name.casefold())[:MAX_MATCHES]
        if not found:
            p.out("  no bank matches; try another search")
            continue
        for i, b in enumerate(found, 1):
            p.out(f"  {i:>2}. {b.name}")
        choice = p.ask("Number of your bank (empty to search again): ")
        if choice.isdigit() and 1 <= int(choice) <= len(found):
            return found[int(choice) - 1]


def _ask_account(p: Prompter, client, answers: Answers, cache: dict[str, list[Aspsp]]) -> dict:
    bank = _choose_bank(p, client, answers.country, cache)

    def check_iban(value: str) -> str | None:
        try:
            iban_mod.validate(iban_mod.normalize(value))
        except ValueError as exc:
            return str(exc)
        return None

    iban = iban_mod.normalize(_retry(p, f"IBAN of your account at {bank.name}: ", check_iban))
    taken = {a["name"] for a in answers.accounts}

    def check_name(value: str) -> str | None:
        if not ACCOUNT_NAME.fullmatch(value):
            return "use lowercase letters, digits, - and _"
        return "that name is already used" if value in taken else None

    default = "main" if "main" not in taken else ""
    name = _retry(p, f"Name for this account{' [main]' if default else ''}: ", check_name, default)
    return {"name": name, "bank_name": bank.name, "bank_country": bank.country, "iban": iban}


def _toml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # JSON string escapes are valid TOML basic-string escapes


def render_toml(a: Answers) -> str:
    lines = [f"default_account = {_toml_str(a.accounts[0]['name'])}", "",  # top-level keys precede the first table
             "[enablebanking]", f"app_id = {_toml_str(a.app_id)}", f"key_path = {_toml_str(str(a.key_path))}",
             f"redirect_url = {_toml_str(a.redirect_url)}", f"country = {_toml_str(a.country)}"]
    if a.psu_ip:
        lines.append(f"psu_ip_address = {_toml_str(a.psu_ip)}")
    for acc in a.accounts:
        lines += ["", f"[accounts.{acc['name']}]", f"bank_name = {_toml_str(acc['bank_name'])}",
                  f"bank_country = {_toml_str(acc['bank_country'])}", f"iban = {_toml_str(acc['iban'])}"]
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace, env: Mapping[str, str], prompter: Prompter | None = None,
        client_factory: Callable[[Config], object] | None = None, check_fn: Callable[[Path], int] | None = None) -> int:
    p = prompter or Prompter()
    target = args.config or paths.default_config_target(env)
    try:
        if target.exists() and not args.force and p.ask(f"{target} exists. Overwrite? [y/N] ").lower() not in ("y", "yes"):
            p.out("Nothing changed.")
            return EXIT_FAIL
        p.out("Enable Banking application: register one at https://enablebanking.com/cp/applications and link "
              "your own accounts to it (see documentation/enablebanking-setup.md).")
        a = Answers()
        a.app_id = _retry(p, "Application ID (UUID): ", _check_uuid)
        a.key_path = _ask_key(p, a.app_id)
        a.redirect_url = _retry(p, f"Redirect URL registered on the application [{DEFAULT_REDIRECT}]: ", _check_url,
                                DEFAULT_REDIRECT)
        a.psu_ip = p.ask("Your public IP, sent as Psu-Ip-Address (many banks need it; empty to skip): ")
        a.country = _retry(p, "Country code of your bank [BE]: ",
                           lambda v: None if len(v) == 2 and v.isalpha() else "enter a 2-letter country code",
                           "BE").upper()
        probe = Config(app_id=a.app_id, key_path=a.key_path, redirect_url=a.redirect_url, country=a.country,
                       psu_ip_address=a.psu_ip or None)
        client = (client_factory or (lambda cfg: make_client(cfg, load_signer(cfg))))(probe)
        cache: dict[str, list[Aspsp]] = {}
        try:
            while True:
                a.accounts.append(_ask_account(p, client, a, cache))
                if p.ask("Add another account? [y/N] ").lower() not in ("y", "yes"):
                    break
        finally:
            close = getattr(client, "close", None)
            if close:
                close()
        write_text_atomic(target, render_toml(a))
    except _Abort as stop:
        return stop.code
    except KeyboardInterrupt:
        p.out("\ninterrupted; nothing was written.")
        return EXIT_INTERRUPT
    except EOFError:
        p.out("\ninput ended; nothing was written.")
        return EXIT_FAIL
    except OSError as exc:
        p.out(f"cannot write {target}: {exc.strerror or exc}")
        return EXIT_FAIL
    p.out(f"Wrote {target} (mode 600). Checking it:")
    if check_fn is None:
        from enablebanking_psd2 import cli  # lazy: cli imports this module

        def check_fn(path: Path) -> int:
            return cli.main(["check", "--config", str(path)])
    code = check_fn(target)
    if code == EXIT_OK:
        p.out("Next: run `enablebanking authorize` to log in at your bank once (about 180 days).")
    return code
