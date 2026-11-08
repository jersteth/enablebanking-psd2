"""Demo step orchestration."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Callable, TextIO

from psd2demo import consent
from psd2demo import iban as iban_mod
from psd2demo.config import Config
from psd2demo.eb.client import EnableBankingClient
from psd2demo.eb.errors import ApiAuthError, ApiError, ApiNotFound
from psd2demo.eb.models import Account, Aspsp
from psd2demo.errors import AccountNotFound, BankNotFound, DemoError
from psd2demo.session_store import SessionStore, StoredSession, iban_hash
from psd2demo.transactions import Summary, Txn, normalize, sort_txns, summarize

LOOKBACK_DAYS = 10
REUSE_MIN_REMAINING = timedelta(minutes=5)


class Step(Enum):
    CONFIG = "CONFIG"
    BANK = "BANK"
    CONSENT = "CONSENT"
    ACCOUNT = "ACCOUNT"
    TRANSACTIONS = "TRANSACTIONS"
    EXPORT = "EXPORT"


EXIT_OK, EXIT_FAIL, EXIT_CONFIG, EXIT_INTERRUPT = 0, 1, 2, 130


@dataclass
class StepRecord:
    name: str
    ok: bool
    detail: str


class StepReporter:
    """Prints one line per step (unless quiet) and records steps for --json output."""

    def __init__(self, out: TextIO | None = None, quiet: bool = False):
        self.out = out
        self.quiet = quiet
        self.steps: list[StepRecord] = []

    def _print(self, text: str) -> None:
        if not self.quiet:
            print(text, file=self.out or sys.stderr, flush=True)

    def info(self, text: str) -> None:
        self._print(f"      {text}")

    def notice(self, text: str) -> None:
        """Like info, but also shown in quiet (--json) mode: for lines the user must act on."""
        print(f"      {text}", file=self.out or sys.stderr, flush=True)

    def ok(self, step: Step, detail: str = "") -> None:
        self.steps.append(StepRecord(step.value, True, detail))
        self._print(f"[✓] {step.value:<12} {detail}".rstrip())

    def fail(self, step: Step, err: DemoError | ApiError) -> RunResult:
        self.steps.append(StepRecord(step.value, False, err.message))
        self._print(f"[✗] {step.value:<12} {err.message}")
        if isinstance(err, ApiError):
            self.info(err.describe())
        if err.hint:
            self.info(f"hint: {err.hint}")
        return RunResult(ok=False, failed_step=step, exit_code=EXIT_CONFIG if step is Step.CONFIG else EXIT_FAIL)


@dataclass
class RunResult:
    ok: bool
    failed_step: Step | None = None
    exit_code: int = EXIT_OK
    summary: dict = field(default_factory=dict)
    txns: list[Txn] = field(default_factory=list)
    totals: dict[str, Summary] = field(default_factory=dict)
    raw_pages: list[dict] = field(default_factory=list)


@dataclass
class ConsentHooks:
    """Side effects of the consent step, injectable for tests."""
    capture: Callable[[Config], str]
    open_browser: Callable[[str], None] = consent.open_browser


def default_capture(cfg: Config) -> str:
    if cfg.capture_mode == "listener":
        return consent.capture_listener(cfg.redirect_url, cfg.callback_timeout)
    return consent.capture_paste(cfg.callback_timeout)


def find_bank(client: EnableBankingClient, name: str, country: str) -> Aspsp:
    aspsps = client.get_aspsps(country)
    wanted = name.casefold()
    for a in aspsps:
        if a.name.casefold() == wanted:
            return a
    names = [a.name for a in aspsps]
    candidates = [n for n in names if wanted in n.casefold()] or names
    raise BankNotFound(name, country, candidates[:10])


def describe_validity(seconds: int | None) -> str:
    if not seconds:
        return "max consent validity not published"
    return f"max consent validity {seconds // 86400} days"


def select_psu_headers(available: dict[str, str], required: list[str]) -> tuple[dict[str, str], list[str]]:
    """Enable Banking rejects a partial set: send every required PSU header, or none.

    Returns the headers to send and the required headers that are missing.
    """
    have = {k.casefold() for k in available}
    missing = [h for h in required if h.casefold() not in have]
    return ({} if missing else available), missing


def _bank_step(cfg: Config, client: EnableBankingClient, reporter: StepReporter) -> Aspsp:
    bank = find_bank(client, cfg.bank_name, cfg.bank_country)
    reporter.ok(Step.BANK, f"{bank.name} ({bank.country}), {describe_validity(bank.maximum_consent_validity)}")
    if bank.required_psu_headers:
        reporter.info("bank requires PSU headers: " + ", ".join(bank.required_psu_headers))
    client.psu_headers, missing = select_psu_headers(client.psu_headers, bank.required_psu_headers)
    if missing:
        reporter.info(f"not configured: {', '.join(missing)} -> sending no PSU headers (unattended access; "
                      "banks may allow only 4 such requests per day). Set psu_ip_address to fix.")
    return bank


def run_check(cfg: Config, client: EnableBankingClient, reporter: StepReporter) -> RunResult:
    try:
        bank = _bank_step(cfg, client, reporter)
    except (DemoError, ApiError) as exc:
        return reporter.fail(Step.BANK, exc)
    return RunResult(ok=True, summary={"bank": bank.name, "country": bank.country,
                                       "maximum_consent_validity": bank.maximum_consent_validity})


def _try_reuse(cfg: Config, client: EnableBankingClient, store: SessionStore, bank: Aspsp,
               now: datetime) -> StoredSession | None:
    stored = store.load()
    if stored is None or not stored.matches(cfg.app_id, bank.name, bank.country, cfg.iban):
        return None
    try:
        status = client.get_session(stored.session_id)
    except (ApiAuthError, ApiNotFound):
        status = None
    if (status is None or status.status != "AUTHORIZED" or status.valid_until is None
            or status.valid_until <= now + REUSE_MIN_REMAINING or stored.account_uid not in status.account_uids):
        store.clear()
        return None
    return stored


def _select_account(cfg: Config, accounts: list[Account]) -> Account:
    for acc in accounts:
        if acc.iban and iban_mod.normalize(acc.iban) == cfg.iban:
            return acc
    listing = [f"{iban_mod.mask(iban_mod.normalize(a.iban)) if a.iban else '(no IBAN)'} "
               f"{a.name or '-'} {a.currency or ''}".strip() for a in accounts]
    raise AccountNotFound(iban_mod.mask(cfg.iban), listing)


def run_demo(cfg: Config, client: EnableBankingClient, reporter: StepReporter, *, fresh: bool = False,
             store: SessionStore | None = None, hooks: ConsentHooks | None = None,
             today: date | None = None, now: datetime | None = None) -> RunResult:
    store = store or SessionStore(cfg.state_dir / "session.json")
    hooks = hooks or ConsentHooks(capture=default_capture)
    today = today or date.today()
    now = now or datetime.now(timezone.utc)
    masked = iban_mod.mask(cfg.iban)
    date_from, date_to = today - timedelta(days=LOOKBACK_DAYS), today

    step = Step.BANK
    try:
        bank = _bank_step(cfg, client, reporter)

        step = Step.CONSENT
        stored = None if fresh else _try_reuse(cfg, client, store, bank, now)
        if stored:
            reporter.ok(Step.CONSENT, f"reused session valid until {stored.valid_until}")
            reporter.ok(Step.ACCOUNT, f"{masked} (uid {stored.account_uid})")
        else:
            state = consent.new_state()
            valid_until = consent.compute_valid_until(now, cfg.consent_days, bank.maximum_consent_validity)
            auth = client.start_auth(bank, valid_until=valid_until, state=state, redirect_url=cfg.redirect_url)
            reporter.notice("approve access at your bank in the browser; if it does not open, visit:")
            reporter.notice(auth.url)
            hooks.open_browser(auth.url)
            if cfg.capture_mode == "listener":
                reporter.notice(f"waiting for the bank redirect on {cfg.redirect_url} ...")
            session = client.create_session(consent.parse_callback(hooks.capture(cfg), state))
            session_valid_until = session.valid_until.isoformat() if session.valid_until else ""
            reporter.ok(Step.CONSENT, f"new session valid until {session_valid_until or 'unknown'}")

            step = Step.ACCOUNT
            account = _select_account(cfg, session.accounts)
            stored = StoredSession(session_id=session.session_id, app_id=cfg.app_id, bank_name=bank.name,
                                   bank_country=bank.country, iban_sha256=iban_hash(cfg.iban),
                                   account_uid=account.uid, valid_until=session_valid_until,
                                   created_at=now.isoformat())
            try:
                store.save(stored)
            except OSError as exc:
                reporter.notice(f"warning: session not saved ({exc.strerror or exc}); "
                                f"the next run will need a new consent. Check {store.path.parent}")
            reporter.ok(Step.ACCOUNT, f"{masked} {account.name or '-'} {account.currency or ''}".rstrip()
                        + f" (uid {account.uid})")

        step = Step.TRANSACTIONS
        pages = list(client.iter_transactions(stored.account_uid, date_from, date_to))
    except (DemoError, ApiError) as exc:
        return reporter.fail(step, exc)

    txns = sort_txns([normalize(t, date_from, date_to) for p in pages for t in p.transactions])
    detail = (f"{len(txns)} transactions" if txns else "no transactions in range") + \
        f" {date_from.isoformat()}..{date_to.isoformat()}, {len(pages)} page(s)"
    if pages and pages[-1].truncated:
        detail += " (truncated: page limit reached)"
    reporter.ok(Step.TRANSACTIONS, detail)

    totals = summarize(txns)
    summary = {
        "bank": bank.name, "country": bank.country, "iban_masked": masked,
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "count": len(txns),
        "totals": {cur: {"count": s.count, "credits": s.credits, "debits": s.debits, "net": s.net}
                   for cur, s in totals.items()},
    }
    return RunResult(ok=True, summary=summary, txns=txns, totals=totals, raw_pages=[p.raw for p in pages])
