import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

FIXTURES = Path(__file__).parent / "fixtures"
VALID_IBAN = "BE68539007547034"
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

from psd2demo.config import Config  # noqa: E402
from psd2demo.eb.client import EnableBankingClient  # noqa: E402
from psd2demo.eb.models import Aspsp, AuthStart, Session, TransactionsPage  # noqa: E402
from psd2demo.session_store import SessionStore, StoredSession, iban_hash  # noqa: E402

BASE = EnableBankingClient.BASE_URL
BANKS = [Aspsp("KBC", "BE", 15552000), Aspsp("KBC Brussels", "BE"), Aspsp("Belfius", "BE")]


class FakeClient:
    """Stands in for EnableBankingClient in flow tests; records calls."""

    def __init__(self, aspsps=BANKS, pages=None, session_status=None, exc=None):
        self.aspsps = aspsps
        self.pages = pages if pages is not None else [
            TransactionsPage.from_json(load_fixture("transactions_p1.json")),
            TransactionsPage.from_json(load_fixture("transactions_p2.json"))]
        self.session_status = session_status
        self.exc = exc  # raised by get_aspsps (if not None) and get_session
        self.calls = []
        self.state = None
        self.psu_headers = {"Psu-User-Agent": "test"}

    def get_aspsps(self, country, psu_type="personal"):
        if self.exc and not self.session_status:
            raise self.exc
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
        if self.exc:
            raise self.exc
        return self.session_status

    def iter_transactions(self, uid, date_from, date_to, max_pages=50):
        self.calls.append(("transactions", uid, date_from, date_to))
        return iter(self.pages)


def make_cfg(tmp_path, bank="KBC", iban=VALID_IBAN) -> Config:
    return Config(app_id=APP_ID, key_path=tmp_path / "k.pem", redirect_url="https://localhost/callback",
                  bank_name=bank, iban=iban, state_dir=tmp_path / ".psd2demo")


def make_store(tmp_path) -> SessionStore:
    return SessionStore(tmp_path / ".psd2demo" / "session.json")


def stored_session(**overrides) -> StoredSession:
    values = dict(session_id="old-session", app_id=APP_ID, bank_name="KBC", bank_country="BE",
                  iban_sha256=iban_hash(VALID_IBAN), account_uid="acc-uid-1",
                  valid_until="2026-11-09T10:00:00+00:00", created_at="2026-11-08T10:00:00+00:00")
    return StoredSession(**{**values, **overrides})


def cli_args(command, key_file, *extra):
    return [command, "--app-id", APP_ID, "--key", str(key_file), "--redirect-url", "https://localhost/callback",
            "--bank", "KBC", "--iban", VALID_IBAN, *extra]
