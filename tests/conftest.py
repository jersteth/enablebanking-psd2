import argparse
import io
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from psd2demo.cli import parse_args
from psd2demo.config import AccountSpec, Config
from psd2demo.eb.client import EnableBankingClient
from psd2demo.eb.models import AccountDetails, Aspsp, AuthStart, Balance, Session, SessionStatus, TransactionsPage
from psd2demo.flow import ConsentHooks, Context, StepReporter
from psd2demo.session_store import SessionRecord, SessionStore, StoredAccount, iban_hash

FIXTURES = Path(__file__).parent / "fixtures"
VALID_IBAN = "BE68539007547034"  # published example IBANs only
OTHER_IBAN = "BE71096123456769"
FOREIGN_IBAN = "DE89370400440532013000"
APP_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(scope="session")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def pem_bytes(rsa_key):
    return rsa_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption())


@pytest.fixture
def key_file(tmp_path, pem_bytes):
    p = tmp_path / f"{APP_ID}.pem"
    p.write_bytes(pem_bytes)
    p.chmod(0o600)
    return p


# --- shared test doubles -----------------------------------------------------------------------------

BASE = EnableBankingClient.BASE_URL
BANKS = [Aspsp("KBC", "BE", 15552000), Aspsp("KBC Brussels", "BE"), Aspsp("Belfius", "BE")]
VALID_STATUS = SessionStatus.from_json(load_fixture("session_get.json"))


class FakeClient:
    """Stands in for EnableBankingClient in flow and command tests; records calls in ``calls``.

    ``aspsps_exc`` / ``session_exc`` / ``tx_exc`` are raised by get_aspsps / get_session / iter_transactions;
    ``delete_errors`` maps a session id to the exception delete_session raises.
    """

    def __init__(self, aspsps=BANKS, pages=None, session_status=None, *, aspsps_exc=None, session_exc=None,
                 tx_exc=None, balances=None, details=None):
        self.aspsps = aspsps
        self.pages = pages if pages is not None else [
            TransactionsPage.from_json(load_fixture("transactions_p1.json")),
            TransactionsPage.from_json(load_fixture("transactions_p2.json"))]
        self.session_status = session_status
        self.aspsps_exc, self.session_exc, self.tx_exc = aspsps_exc, session_exc, tx_exc
        self.balances = balances if balances is not None else [
            Balance.from_json(b) for b in load_fixture("balances.json")["balances"]]
        self.details = details or AccountDetails(load_fixture("details.json"))
        self.calls = []
        self.state = None
        self.psu_headers = {"Psu-User-Agent": "test"}
        self.delete_errors = {}

    def get_aspsps(self, country, psu_type="personal"):
        self.calls.append(("aspsps", country))
        if self.aspsps_exc:
            raise self.aspsps_exc
        return self.aspsps

    def start_auth(self, aspsp, *, valid_until, state, redirect_url, psu_type="personal"):
        self.calls.append(("start_auth", state, valid_until))
        self.state = state
        return AuthStart.from_json(load_fixture("auth.json"))

    def create_session(self, code):
        self.calls.append(("create_session", code))
        return Session.from_json(load_fixture("session_create.json"))

    def get_session(self, session_id):
        self.calls.append(("get_session", session_id))
        if self.session_exc:
            raise self.session_exc
        return self.session_status

    def delete_session(self, session_id):
        self.calls.append(("delete_session", session_id))
        if exc := self.delete_errors.get(session_id):
            raise exc

    def get_balances(self, uid):
        self.calls.append(("balances", uid))
        return self.balances

    def get_account_details(self, uid):
        self.calls.append(("details", uid))
        return self.details

    def iter_transactions(self, uid, date_from, date_to, max_pages=50):
        self.calls.append(("transactions", uid, date_from, date_to))
        if self.tx_exc:
            raise self.tx_exc
        return iter(self.pages)


def make_cfg(tmp_path, bank="KBC", iban=VALID_IBAN, *, accounts=None, **kwargs) -> Config:
    """Config with one account ``main`` (bank, iban), or the given ``accounts`` {name: (bank, iban[, country])}."""
    specs = accounts or {"main": (bank, iban)}
    accs = {name: AccountSpec(name, v[0], v[2] if len(v) > 2 else "BE", v[1]) for name, v in specs.items()}
    return Config(app_id=APP_ID, key_path=tmp_path / "k.pem", redirect_url="https://localhost/callback",
                  accounts=accs, state_dir=tmp_path / ".psd2demo", **kwargs)


def make_store(tmp_path) -> SessionStore:
    return SessionStore(tmp_path / ".psd2demo" / "session.json")


def session_record(**overrides) -> SessionRecord:
    values = dict(session_id="old-session", app_id=APP_ID, bank_name="KBC", bank_country="BE",
                  accounts=(StoredAccount("acc-uid-1", iban_hash(VALID_IBAN)),),
                  valid_until="2026-11-09T10:00:00+00:00", created_at="2026-11-08T10:00:00+00:00")
    return SessionRecord(**{**values, **overrides})


def cli_args(command, key_file, *extra):
    return [command, "--app-id", APP_ID, "--key", str(key_file), "--redirect-url", "https://localhost/callback",
            "--bank", "KBC", "--iban", VALID_IBAN, *extra]


NOW = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
TODAY = date(2026, 11, 8)


def make_ctx(tmp_path, client, cfg=None, *, url_fn=None, quiet=False, now=NOW, today=TODAY, stored=False):
    """A flow Context with captured reporter output and a fake consent capture.

    Returns (ctx, out, opened): ``out`` is the reporter's StringIO, ``opened`` the URLs "opened in the browser".
    By default the capture answers with a valid callback for the client's last /auth state.
    ``stored=True`` adds a usable stored session (session_record() + an AUTHORIZED GET /sessions).
    """
    out, opened = io.StringIO(), []
    url_fn = url_fn or (lambda: f"https://localhost/callback?code=CODE1&state={client.state}")
    ctx = Context(cfg=cfg or make_cfg(tmp_path), client=client, store=make_store(tmp_path),
                  reporter=StepReporter(out, quiet=quiet),
                  hooks=ConsentHooks(capture=lambda cfg: url_fn(), open_browser=opened.append),
                  today=today, now=now)
    if stored:
        ctx.store.upsert(session_record())
        client.session_status = client.session_status or VALID_STATUS
    return ctx, out, opened


def cmd_args(command: str, *argv: str, **overrides) -> argparse.Namespace:
    """The namespace the CLI builds for ``command`` (plus ``argv``), with attributes overridden."""
    args = parse_args([*command.split(), *argv])
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


class PasteStdin:
    """stdin whose next line is the redirect URL carrying the state of the /auth request just made."""

    def __init__(self, auth_route):
        self.auth_route = auth_route

    def readline(self):
        state = json.loads(self.auth_route.calls.last.request.content)["state"]
        return f"https://localhost/callback?code=C1&state={state}\n"
