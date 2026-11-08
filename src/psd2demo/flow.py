"""Step orchestration: the shared session step (bank → consent or reuse → account) and step bodies."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Callable, Literal, TextIO

from psd2demo import consent
from psd2demo import iban as iban_mod
from psd2demo.config import AccountSpec, Config
from psd2demo.eb.client import EnableBankingClient
from psd2demo.eb.errors import ApiAuthError, ApiError, ApiNotFound
from psd2demo.eb.models import Account, Aspsp, SessionStatus, TransactionsPage
from psd2demo.errors import AccountNotFound, BankNotFound, ConfigError, DemoError, UsageError
from psd2demo.session_store import BankMeta, SessionRecord, SessionStore, StoredAccount, iban_hash
from psd2demo.transactions import Summary, Txn, normalize, sort_txns, summarize

DEFAULT_LOOKBACK_DAYS = 10
REUSE_MIN_REMAINING = timedelta(minutes=5)
RANGE_HINT = ("the bank may limit history (often 90 days without a new SCA): "
              "shorten the range or rerun with --fresh")


class Step(Enum):
    CONFIG = "CONFIG"
    BANK = "BANK"
    CONSENT = "CONSENT"
    ACCOUNT = "ACCOUNT"
    TRANSACTIONS = "TRANSACTIONS"
    BALANCES = "BALANCES"
    DETAILS = "DETAILS"
    SESSIONS = "SESSIONS"
    REVOKE = "REVOKE"
    EXPORT = "EXPORT"


EXIT_OK, EXIT_FAIL, EXIT_CONFIG, EXIT_PARTIAL, EXIT_INTERRUPT = 0, 1, 2, 3, 130  # 3: history, some accounts ok


@dataclass
class StepRecord:
    name: str
    ok: bool
    detail: str


@dataclass
class CommandResult:
    ok: bool
    failed_step: Step | None = None
    exit_code: int = EXIT_OK
    summary: dict = field(default_factory=dict)  # JSON-safe after json.dumps(default=str)
    render: Callable[[], str] | None = None  # human output for stdout


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

    def fail(self, step: Step, err: DemoError | ApiError, *, summary: dict | None = None,
             render: Callable[[], str] | None = None) -> CommandResult:
        """Report a failed step. Config and usage errors exit 2, everything else 1; ``summary``/``render``
        keep partial output (e.g. a table whose export failed)."""
        self.steps.append(StepRecord(step.value, False, err.message))
        self._print(f"[✗] {step.value:<12} {err.message}")
        if isinstance(err, ApiError):
            self.info(err.describe())
        if err.hint:
            self.info(f"hint: {err.hint}")
        return CommandResult(ok=False, failed_step=step, summary=summary or {}, render=render,
                             exit_code=EXIT_CONFIG if isinstance(err, (ConfigError, UsageError)) else EXIT_FAIL)


@dataclass
class ConsentHooks:
    """Side effects of the consent step, injectable for tests."""
    capture: Callable[[Config], str]
    open_browser: Callable[[str], None] = consent.open_browser


def default_capture(cfg: Config) -> str:
    if cfg.capture_mode == "listener":
        return consent.capture_listener(cfg.redirect_url, cfg.callback_timeout)
    return consent.capture_paste(cfg.callback_timeout)


@dataclass
class Context:
    """Everything a command needs. ``step`` tracks the running step so one handler can report failures."""
    cfg: Config
    client: EnableBankingClient
    store: SessionStore
    reporter: StepReporter
    today: date
    now: datetime
    hooks: ConsentHooks | None = None  # None: non-interactive (history); a consent raises ConsentRequired
    step: Step = Step.CONFIG
    base_psu_headers: dict[str, str] = field(init=False)  # every PSU header we could send
    live_sessions: dict = field(init=False, default_factory=dict)  # session_id → SessionStatus | ApiError

    def __post_init__(self):
        self.base_psu_headers = dict(self.client.psu_headers)


@dataclass(frozen=True)
class AccountSession:
    account: AccountSpec
    record: SessionRecord
    uid: str
    reused: bool


@dataclass
class TxnResult:
    pages: list[TransactionsPage]
    txns: list[Txn]
    totals: dict[str, Summary]


class ConsentRequired(DemoError):
    def __init__(self, account: AccountSpec):
        super().__init__(f"consent required for account {account.name!r} ({account.bank_name})",
                         hint=f"psd2-demo authorize --account {account.name}")
        self.account = account


def find_bank(client: EnableBankingClient, name: str, country: str) -> Aspsp:
    aspsps = client.get_aspsps(country)
    wanted = name.casefold()
    for a in aspsps:
        if a.name.casefold() == wanted:
            return a
    names = [a.name for a in aspsps]
    candidates = [n for n in names if wanted in n.casefold()] or names
    raise BankNotFound(name, country, candidates[:10])


def describe_validity(bank: Aspsp) -> str:
    if not bank.max_consent_days:
        return "max consent validity not published"
    return f"max consent validity {bank.max_consent_days} days"


def select_psu_headers(available: dict[str, str], required: list[str] | tuple[str, ...]
                       ) -> tuple[dict[str, str], list[str]]:
    """Enable Banking rejects a partial set: send every required PSU header, or none.

    Returns the headers to send and the required headers that are missing.
    """
    have = {k.casefold() for k in available}
    missing = [h for h in required if h.casefold() not in have]
    return ({} if missing else available), missing


def bank_for(ctx: Context, account: AccountSpec) -> Aspsp:
    """BANK step: look the bank up and apply its PSU header rule to the client."""
    ctx.step = Step.BANK
    bank = find_bank(ctx.client, account.bank_name, account.bank_country)
    ctx.reporter.ok(Step.BANK, f"{bank.name} ({bank.country}), {describe_validity(bank)}")
    _apply_psu(ctx, bank.required_psu_headers)
    return bank


def _apply_psu(ctx: Context, required) -> None:
    if required:
        ctx.reporter.info("bank requires PSU headers: " + ", ".join(required))
    ctx.client.psu_headers, missing = select_psu_headers(ctx.base_psu_headers, required)
    if missing:
        ctx.reporter.info(f"not configured: {', '.join(missing)} -> sending no PSU headers (unattended access; "
                          "banks may allow only 4 such requests per day). Set psu_ip_address to fix.")


def _masked(a: Account) -> str:
    return iban_mod.mask(iban_mod.normalize(a.iban))


def live_session(ctx: Context, session_id: str) -> SessionStatus | None:
    """GET /sessions/{id} once per context (accounts of one bank share a session); None if gone (401/403/404).
    Other API errors are raised, also on repeated lookups."""
    if session_id not in ctx.live_sessions:
        try:
            ctx.live_sessions[session_id] = ctx.client.get_session(session_id)
        except (ApiAuthError, ApiNotFound):
            ctx.live_sessions[session_id] = None
        except ApiError as exc:
            ctx.live_sessions[session_id] = exc
    result = ctx.live_sessions[session_id]
    if isinstance(result, ApiError):
        raise result
    return result


def session_state(ctx: Context, rec: SessionRecord | None, iban: str,
                  margin: timedelta = REUSE_MIN_REMAINING) -> str | None:
    """The account uid if ``rec`` is usable for ``iban`` for at least ``margin`` (locally, then live), else None."""
    uid = rec.account_uid(iban) if rec else None
    if not uid or not rec.valid_at(ctx.now, margin):
        return None
    live = live_session(ctx, rec.session_id)
    if (live is None or live.status != "AUTHORIZED" or live.valid_until is None
            or live.valid_until <= ctx.now + margin or uid not in live.account_uids):
        return None
    return uid


def _consent(ctx: Context, bank: Aspsp, consent_days: int | Literal["max"] | None
             ) -> tuple[SessionRecord, list[Account]]:
    """Interactive consent at the bank; stores one record covering every account the user authorized."""
    cfg, client, reporter = ctx.cfg, ctx.client, ctx.reporter
    days = consent_days or cfg.consent_days
    if consent_days == "max":
        days = None if bank.maximum_consent_validity else cfg.consent_days  # None: the bank's maximum
    state = consent.new_state()
    valid_until = consent.compute_valid_until(ctx.now, days, bank.maximum_consent_validity)
    auth = client.start_auth(bank, valid_until=valid_until, state=state, redirect_url=cfg.redirect_url)
    reporter.notice("approve access at your bank in the browser; if it does not open, visit:")
    reporter.notice(auth.url)
    ctx.hooks.open_browser(auth.url)
    if cfg.capture_mode == "listener":
        reporter.notice(f"waiting for the bank redirect on {cfg.redirect_url} ...")
    session = client.create_session(consent.parse_callback(ctx.hooks.capture(cfg), state))
    record = SessionRecord(
        session_id=session.session_id, app_id=cfg.app_id, bank_name=bank.name, bank_country=bank.country,
        accounts=tuple(StoredAccount(a.uid, iban_hash(a.iban), _masked(a), a.name, a.currency, a.cash_account_type)
                       for a in session.accounts if a.iban),
        valid_until=session.valid_until.isoformat() if session.valid_until else "",
        created_at=ctx.now.isoformat(),
        bank=BankMeta(bank.maximum_consent_validity, tuple(bank.required_psu_headers)))
    try:
        ctx.store.upsert(record)
    except OSError as exc:
        reporter.notice(f"warning: session not saved ({exc.strerror or exc}); "
                        f"the next run will need a new consent. Check {ctx.store.path.parent}")
    reporter.ok(Step.CONSENT, f"new session valid until {record.valid_until or 'unknown'}")
    return record, session.accounts


def consent_bank(ctx: Context, account: AccountSpec, consent_days: int | Literal["max"] | None = None, *,
                 bank: Aspsp | None = None) -> tuple[SessionRecord, list[Account]]:
    """Interactive consent at ``account``'s bank, stored as that bank's record (covering every account the user
    selected at the bank). No account matching: callers check which accounts the record covers."""
    bank = bank or bank_for(ctx, account)
    ctx.step = Step.CONSENT
    return _consent(ctx, bank, consent_days)


def ensure_session(ctx: Context, account: AccountSpec, *, interactive: bool = True, fresh: bool = False,
                   consent_days: int | Literal["max"] | None = None) -> AccountSession:
    """BANK → CONSENT (reuse the bank's session, or a new consent if interactive) → ACCOUNT.

    ``consent_days``: None uses the config value, "max" the bank's maximum validity.
    """
    masked = iban_mod.mask(account.iban)
    # Reuse first: a stored record carries the bank's limits and PSU rule, so GET /aspsps is only needed
    # for a new consent (or a record migrated from 002 without bank metadata).
    rec = ctx.store.for_bank(ctx.cfg.app_id, account.bank_name, account.bank_country)
    if not fresh and rec and rec.bank:
        ctx.step = Step.BANK
        ctx.reporter.ok(Step.BANK, f"{rec.bank_name} ({rec.bank_country}), from the stored session")
        _apply_psu(ctx, rec.bank.required_psu_headers)
        bank = None
    else:
        bank = bank_for(ctx, account)

    ctx.step = Step.CONSENT
    uid = None if fresh else session_state(ctx, rec, account.iban)
    if uid:
        ctx.reporter.ok(Step.CONSENT, f"reused session valid until {rec.valid_until}")
        ctx.reporter.ok(Step.ACCOUNT, f"{account.name} {masked} (uid {uid})")
        return AccountSession(account, rec, uid, reused=True)
    if not interactive or ctx.hooks is None:
        raise ConsentRequired(account)
    rec, session_accounts = consent_bank(ctx, account, consent_days, bank=bank)
    ctx.step = Step.ACCOUNT
    match = next((a for a in session_accounts if a.iban and iban_mod.normalize(a.iban) == account.iban), None)
    if match is None:
        listing = [f"{_masked(a) if a.iban else '(no IBAN)'} {a.name or '-'} {a.currency or ''}".strip()
                   for a in session_accounts]
        raise AccountNotFound(masked, listing)
    ctx.reporter.ok(Step.ACCOUNT, f"{account.name} {masked} {match.name or '-'} {match.currency or ''}".rstrip()
                    + f" (uid {match.uid})")
    return AccountSession(account, rec, match.uid, reused=False)


def fetch_transactions(ctx: Context, a: AccountSession, date_from: date, date_to: date
                       ) -> tuple[list[TransactionsPage], list[dict], list[Txn]]:
    """TRANSACTIONS step body: (pages, raw transactions, normalized transactions), both lists in API order.
    A 400/422 without a more specific hint gets the history-limit hint."""
    ctx.step = Step.TRANSACTIONS
    try:
        pages = list(ctx.client.iter_transactions(a.uid, date_from, date_to))
    except ApiError as exc:
        if exc.status in (400, 422) and exc.hint is None:
            exc.hint = RANGE_HINT
        raise
    raws = [t for p in pages for t in p.transactions]
    return pages, raws, [normalize(t, date_from, date_to) for t in raws]


def run_transactions(ctx: Context, a: AccountSession, date_from: date, date_to: date) -> TxnResult:
    pages, _, txns = fetch_transactions(ctx, a, date_from, date_to)
    txns = sort_txns(txns)
    detail = (f"{len(txns)} transactions" if txns else "no transactions in range") + \
        f" {date_from.isoformat()}..{date_to.isoformat()}, {len(pages)} page(s)"
    if pages and pages[-1].truncated:
        detail += " (truncated: page limit reached)"
    ctx.reporter.ok(Step.TRANSACTIONS, detail)
    return TxnResult(pages, txns, summarize(txns))


def totals_json(totals: dict[str, Summary]) -> dict:
    return {cur: {"count": s.count, "credits": s.credits, "debits": s.debits, "net": s.net}
            for cur, s in totals.items()}
