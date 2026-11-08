"""Non-interactive account history: every selected account → one versioned JSON document (contract.py).

Never prompts, never opens a browser, never starts a consent and never writes the session store: an account
without a usable stored session is reported as ``consent_required`` (fix: ``enablebanking authorize``).
The CLI ``history`` command and Python applications both call :func:`fetch_history`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import cache
from typing import Callable, Mapping, Sequence, TextIO

from enablebanking_psd2 import __version__, contract
from enablebanking_psd2.bootstrap import load_signer, make_client
from enablebanking_psd2.cache import HistoryCache
from enablebanking_psd2.config import HISTORY_PARTS, NO_ACCOUNTS, AccountSpec, Config, load_config, parse_include
from enablebanking_psd2.daterange import resolve_range
from enablebanking_psd2.eb.client import EnableBankingClient
from enablebanking_psd2.eb.errors import ApiError, ApiRateLimited
from enablebanking_psd2.errors import ConfigError, DemoError, UsageError
from enablebanking_psd2.flow import (REUSE_MIN_REMAINING, ConsentRequired, Context, Step, StepReporter, ensure_session,
                           fetch_transactions)
from enablebanking_psd2.log import redact, set_own_ibans
from enablebanking_psd2.session_store import SessionStore, iban_hash


@dataclass(frozen=True)
class HistoryRequest:
    accounts: tuple[AccountSpec, ...]
    date_from: date
    date_to: date
    include: frozenset[str]
    max_age: int  # minutes a cached entry may be old
    use_cache: bool = True


def _error(exc: Exception, step: Step | str) -> dict:
    if isinstance(exc, ApiError):
        return contract.error(step, exc.message, http_status=exc.status, api_error=exc.error, hint=exc.hint)
    if isinstance(exc, DemoError):
        return contract.error(step, exc.message, hint=exc.hint)
    return contract.error("internal", redact(f"{type(exc).__name__}: {exc}"))


def _fetch_account(account: AccountSpec, ctx: Callable[[], Context], cfg: Config, store: SessionStore,
                   cache_: HistoryCache, req: HistoryRequest, now: datetime) -> dict:
    """One account's entry: stored session (local check) → cache → API (session check, transactions, parts)."""
    warnings: list[dict] = []

    def entry(status: str, **kw) -> dict:
        return contract.account_entry(account=account, status=status, include=req.include, warnings=warnings, **kw)

    rec = store.for_account(cfg.app_id, account)
    if rec is None or not rec.valid_at(now, REUSE_MIN_REMAINING):  # decided locally: no client, no API call
        return entry("consent_required", error=_error(ConsentRequired(account), "SESSION"))
    # The session id is part of the key, so a re-authorized consent never serves the old consent's data.
    key = HistoryCache.key(cfg.app_id, f"{iban_hash(account.iban)}:{rec.session_id}", req.date_from, req.date_to,
                           req.include)
    if req.use_cache and (hit := cache_.get(key, req.max_age, now)) is not None:
        return {**hit, "cached": True, "stale": False, "api_calls": 0}

    c = ctx()
    calls0 = c.client.request_count

    def part(name: str, fetch: Callable[[], object]):
        """An optional part: None unless requested; a failure (also a 429) → None plus a warning."""
        if name not in req.include:
            return None
        c.step = Step(name.upper())
        try:
            return fetch()
        except ApiError as exc:
            warnings.append({"part": name, "message": exc.message, "http_status": exc.status or None})
            return None

    try:
        session = ensure_session(c, account, interactive=False)
        pages, raws, txns = fetch_transactions(c, session, req.date_from, req.date_to)
        balances = part("balances", lambda: [contract.balance(b) for b in c.client.get_balances(session.uid)])
        details = part("details", lambda: c.client.get_account_details(session.uid).flattened())
    except ConsentRequired as exc:
        return entry("consent_required", error=_error(exc, "SESSION"))
    except (ApiError, DemoError) as exc:
        step = contract.error_step(c.step)
        if not isinstance(exc, ApiRateLimited):
            return entry("error", error=_error(exc, step))
        stale = cache_.get(key, None, now) if req.use_cache else None
        if stale is None:
            return entry("rate_limited", error=_error(exc, step))
        warning = {"part": "account", "http_status": exc.status,
                   "message": f"rate limited; serving the cached result from {stale['fetched_at']}"}
        return {**stale, "cached": True, "stale": True, "api_calls": 0, "warnings": [*stale["warnings"], warning]}

    truncated = bool(pages and pages[-1].truncated)
    if truncated:
        warnings.insert(0, {"part": "transactions", "http_status": None,
                            "message": "stopped paging (repeated key or page limit); more data may exist"})
    result = entry("ok", bank=session.record.bank_name, session_valid_until=session.record.valid_until or None,
                   fetched_at=now, truncated=truncated, api_calls=c.client.request_count - calls0,
                   transactions=contract.transactions(account, txns, raws if "raw" in req.include else None),
                   balances=balances, details=details)
    if req.use_cache and not warnings:  # don't freeze a partial result for max_age minutes
        cache_.put(key, result, now)
    return result


def build_document(cfg: Config, client: Callable[[], EnableBankingClient], store: SessionStore,
                   cache_: HistoryCache, req: HistoryRequest, *, now: datetime,
                   reporter: StepReporter | None = None) -> dict:
    """The history document for ``req``. Accounts are processed sequentially and independently; ``client`` is a
    factory called at most once, on first need (a fully cached run makes no network call)."""
    reporter = reporter or StepReporter(quiet=True)

    @cache
    def ctx() -> Context:  # one context per run: accounts sharing a bank session check it once
        return Context(cfg=cfg, client=client(), store=store, reporter=reporter, today=req.date_to, now=now)

    entries = []
    for account in req.accounts:
        try:
            entry = _fetch_account(account, ctx, cfg, store, cache_, req, now)
        except Exception as exc:  # isolation: one account's bug never sinks the others
            entry = contract.account_entry(account=account, status="error", error=_error(exc, "internal"),
                                           include=req.include)
        reporter.info(f"{account.name}: {entry['status']}"
                      + (" (cached)" if entry["cached"] else f", {entry['api_calls']} API call(s)"))
        entries.append(entry)
    return contract.document(generated_at=now, tool_version=__version__, date_from=req.date_from,
                             date_to=req.date_to, include=req.include, entries=entries)


def resolve_include(include: Sequence[str] | str | None, default: frozenset[str]) -> frozenset[str]:
    """None → the config default; a list or comma-separated string ("none" = empty) replaces it."""
    if include is None:
        return default
    parts, unknown = parse_include(include)
    if unknown:
        raise ConfigError([f"unknown include part(s) {sorted(unknown)}; allowed: {sorted(HISTORY_PARTS)}"])
    return parts


def fetch_history(config_path=None, accounts: Sequence[str] | None = None, days: int | None = None,
                  date_from: date | None = None, date_to: date | None = None,
                  include: Sequence[str] | str | None = None, *, max_age: int | None = None,
                  use_cache: bool = True, env: Mapping[str, str] | None = None, client=None,
                  today: date | None = None, now: datetime | None = None, progress: TextIO | None = None) -> dict:
    """Fetch the history of the configured accounts as a schema-v1 document (see schemas/).

    Prints nothing to stdout and raises only ConfigError (bad config, include, range or account name) and
    KeyboardInterrupt; every per-account problem is reported inside the document. ``client``: an injected
    client (tests, embedding); otherwise one is created on first need and closed afterwards. ``progress``: a
    stream for per-account progress lines (the CLI passes stderr with --verbose).
    """
    cfg = load_config(config_path, os.environ if env is None else env, {})
    now = now or datetime.now(timezone.utc)
    try:
        date_from, date_to = resolve_range(today or now.astimezone().date(), days=days, date_from=date_from,
                                           date_to=date_to, default_days=cfg.history.days)
    except UsageError as exc:
        raise ConfigError([exc.message]) from exc
    targets = tuple(cfg.select(accounts))
    if not targets:
        raise ConfigError([NO_ACCOUNTS])
    req = HistoryRequest(accounts=targets, date_from=date_from, date_to=date_to,
                         include=resolve_include(include, cfg.history.include),
                         max_age=cfg.history.max_age if max_age is None else max_age, use_cache=use_cache)
    set_own_ibans(a.iban for a in cfg.accounts.values())
    created: list[EnableBankingClient] = []

    def factory() -> EnableBankingClient:
        if client is not None:
            return client
        created.append(make_client(cfg, load_signer(cfg), retry_rate_limits=False))
        return created[0]

    try:
        return build_document(cfg, factory, SessionStore(cfg.state_dir / "session.json"),
                              HistoryCache(cfg.state_dir / "cache"), req, now=now,
                              reporter=StepReporter(out=progress, quiet=progress is None))
    finally:
        for c in created:
            c.close()
