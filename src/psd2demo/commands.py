"""Commands: each turns parsed arguments into a CommandResult (summary for --json, render for humans).

Pattern: one ``try`` around the steps, ``except (DemoError, ApiError)`` → ``reporter.fail(ctx.step, exc)``;
``ctx.step`` is set by whichever step runs (it starts at CONFIG, which covers account and range resolution).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Callable

from psd2demo import iban as iban_mod
from psd2demo.cache import HistoryCache
from psd2demo.consent import prompt_stderr
from psd2demo.daterange import resolve_range
from psd2demo.config import NO_ACCOUNTS, AccountSpec
from psd2demo.eb.errors import ApiError, ApiNotFound
from psd2demo.errors import ConfigError, DemoError, UsageError
from psd2demo.flow import (DEFAULT_LOOKBACK_DAYS, EXIT_CONFIG, EXIT_FAIL, EXIT_OK, EXIT_PARTIAL, AccountSession,
                           CommandResult, Context, Step, bank_for, consent_bank, ensure_session, run_transactions,
                           session_state, totals_json)
from psd2demo.fileio import write_text_atomic
from psd2demo.history import fetch_history
from psd2demo.output import (export_balances, export_json, render_accounts,
                             render_authorize, render_balances, render_banks, render_details, render_sessions,
                             render_summary, render_transactions)
from psd2demo.session_store import SessionRecord

EXPORTS_DIR = Path("exports")
CACHE_RETENTION = timedelta(days=7)
RENEW_WITHIN = timedelta(days=7)  # authorize renews consents that expire sooner than this


def _account_session(ctx: Context, args: argparse.Namespace) -> AccountSession:
    """The selected account with a usable session (reused or newly consented)."""
    return ensure_session(ctx, ctx.cfg.account(args.account), fresh=args.fresh)


def _meta(s: AccountSession) -> dict:
    """Fields every account command's summary and export start with."""
    return {"account": s.account.name, "bank": s.record.bank_name, "country": s.record.bank_country,
            "iban_masked": iban_mod.mask(s.account.iban)}


def _outside_exports_note(path: Path) -> str | None:
    """A warning when ``path`` is not under the git-ignored exports/ directory."""
    if EXPORTS_DIR.resolve() in path.resolve().parents:
        return None
    return f"note: {path} is outside exports/, so it is not git-ignored; it contains personal financial data"


def _export(ctx: Context, path: Path, write: Callable[[Path], None], result: CommandResult) -> CommandResult:
    """EXPORT step: write the file; a failure fails the command but keeps its rendered output."""
    ctx.step = Step.EXPORT
    if note := _outside_exports_note(path):
        ctx.reporter.notice(note)
    try:
        write(path)
    except OSError as exc:
        return ctx.reporter.fail(Step.EXPORT, DemoError(f"cannot write {path}: {exc.strerror or exc}"),
                                 summary=result.summary, render=result.render)
    ctx.reporter.ok(Step.EXPORT, str(path))
    return result


def check(ctx: Context, args: argparse.Namespace) -> CommandResult:
    """Credentials and key; the bank of the selected account when one can be selected."""
    try:
        account = ctx.cfg.account(args.account, required=False)
        if account is None:
            ctx.step = Step.BANK
            banks = ctx.client.get_aspsps("BE")
            ctx.reporter.ok(Step.BANK, f"credentials ok, {len(banks)} Belgian banks (no account selected)")
            return CommandResult(ok=True, summary={"banks": len(banks)})
        bank = bank_for(ctx, account)
    except (DemoError, ApiError) as exc:
        return ctx.reporter.fail(ctx.step, exc)
    return CommandResult(ok=True, summary={"account": account.name, "bank": bank.name, "country": bank.country,
                                           "maximum_consent_validity": bank.maximum_consent_validity,
                                           "required_psu_headers": bank.required_psu_headers})


def run(ctx: Context, args: argparse.Namespace) -> CommandResult:
    """Transactions of the selected account over a date range (default: last 10 days)."""
    try:
        date_from, date_to = resolve_range(ctx.today, days=args.days, date_from=args.date_from,
                                           date_to=args.date_to, default_days=DEFAULT_LOOKBACK_DAYS)
        session = _account_session(ctx, args)
        tx = run_transactions(ctx, session, date_from, date_to)
    except (DemoError, ApiError) as exc:
        return ctx.reporter.fail(ctx.step, exc)

    summary = {**_meta(session), "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
               "count": len(tx.txns), "totals": totals_json(tx.totals)}
    result = CommandResult(
        ok=True, summary=summary,
        render=lambda: f"\n{render_transactions(tx.txns)}\n\n{render_summary(tx.totals)}")
    if args.export:
        return _export(ctx, args.export, lambda p: export_json(p, summary, tx.txns, [pg.raw for pg in tx.pages]),
                       result)
    return result


def banks(ctx: Context, args: argparse.Namespace) -> CommandResult:
    """Personal-PSU banks of a country, sorted, optionally filtered by name substring. Needs no account."""
    try:
        ctx.step = Step.BANK
        found = ctx.client.get_aspsps((args.country or "BE").upper())
    except (DemoError, ApiError) as exc:
        return ctx.reporter.fail(ctx.step, exc)
    wanted = (args.filter or "").casefold()
    found = sorted((b for b in found if wanted in b.name.casefold()), key=lambda b: b.name.casefold())
    ctx.reporter.ok(Step.BANK, f"{len(found)} bank(s)")
    summary = {"banks": [{"name": b.name, "country": b.country, "bic": b.bic,
                          "maximum_consent_validity_days": b.max_consent_days,
                          "required_psu_headers": b.required_psu_headers, "beta": b.beta} for b in found]}
    return CommandResult(ok=True, summary=summary, render=lambda: render_banks(found))


def balances(ctx: Context, args: argparse.Namespace) -> CommandResult:
    try:
        session = _account_session(ctx, args)
        ctx.step = Step.BALANCES
        found = ctx.client.get_balances(session.uid)
    except (DemoError, ApiError) as exc:
        return ctx.reporter.fail(ctx.step, exc)
    ctx.reporter.ok(Step.BALANCES, f"{len(found)} balance(s)" if found else "the bank returned no balances")
    meta = _meta(session)
    summary = {**meta, "balances": [{"type": b.balance_type, "type_label": b.type_label,
                                     "name": b.name, "amount": b.amount, "currency": b.currency,
                                     "reference_date": b.reference_date, "last_change": b.last_change}
                                    for b in found]}
    result = CommandResult(ok=True, summary=summary, render=lambda: render_balances(found))
    if args.export:
        return _export(ctx, args.export, lambda p: export_balances(p, meta, found), result)
    return result


def accounts(ctx: Context, args: argparse.Namespace) -> CommandResult:
    """The accounts the selected account's consent covers; --details adds the bank's account resource."""
    try:
        session = _account_session(ctx, args)
        details = None
        if args.details:
            ctx.step = Step.DETAILS
            raw = ctx.client.get_account_details(session.uid).flattened()
            details = {k: iban_mod.mask_others(v, session.account.iban) for k, v in raw.items()}
            ctx.reporter.ok(Step.DETAILS, f"{len(details)} field(s)")
    except (DemoError, ApiError) as exc:
        return ctx.reporter.fail(ctx.step, exc)
    rows = []
    for a in session.record.accounts:
        selected = a.uid == session.uid
        rows.append({"selected": selected, "uid": a.uid,
                     "iban": session.account.iban if selected else (a.iban_masked or None),
                     "name": a.name, "currency": a.currency, "cash_account_type": a.cash_account_type})
    summary = {**_meta(session), "accounts": rows, "details": details}

    def render() -> str:
        text = render_accounts(rows)
        return f"{text}\n\n{render_details(details)}" if details is not None else text

    return CommandResult(ok=True, summary=summary, render=render)


def sessions_list(ctx: Context, args: argparse.Namespace) -> CommandResult:
    """Stored consents with local and (unless --offline) live status."""
    ctx.step = Step.SESSIONS
    rows = []
    for rec in ctx.store.records():
        configured = [f"{a.name} {iban_mod.mask(a.iban)}" for a in ctx.cfg.accounts.values()
                      if rec.covers(ctx.cfg.app_id, a)]
        live = None
        if not args.offline and rec.status != "revoked":
            try:
                live = ctx.client.get_session(rec.session_id).status
            except ApiError as exc:
                live = f"unknown ({exc.status or exc.message})"
        rows.append({"bank": rec.bank_name, "country": rec.bank_country, "session": rec.short_id,
                     "accounts": len(rec.accounts), "configured": configured, "valid_until": rec.valid_until or None,
                     "local_status": rec.local_status(ctx.now), "live_status": live})
    ctx.reporter.ok(Step.SESSIONS, f"{len(rows)} stored session(s)")
    return CommandResult(ok=True, summary={"sessions": rows}, render=lambda: render_sessions(rows))


def _revoke_targets(ctx: Context, args: argparse.Namespace) -> list[SessionRecord]:
    records = ctx.store.records()
    if args.all:
        return [r for r in records if r.status == "active"]
    if args.bank:
        return [r for r in records if r.bank_name.casefold() == args.bank.casefold()]
    account = ctx.cfg.account(args.account)
    return [r for r in records if r.same_bank(ctx.cfg.app_id, account.bank_name, account.bank_country)]


def _confirmed(question: str) -> bool:
    try:
        return prompt_stderr(question).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def sessions_revoke(ctx: Context, args: argparse.Namespace) -> CommandResult:
    """DELETE /sessions/{id} for the selected consents. Only ever runs on an explicit request."""
    try:
        if not (args.all or args.bank or args.account):
            raise UsageError("choose what to revoke: --account NAME, --bank NAME or --all")
        targets = _revoke_targets(ctx, args)
        ctx.step = Step.REVOKE
        if not targets:
            raise DemoError("no matching stored session", hint="psd2-demo sessions lists the stored sessions")
        if args.all and not args.yes and not _confirmed(f"Revoke {len(targets)} session(s) at the bank? [y/N] "):
            raise UsageError("aborted; nothing revoked (use --yes to skip the question)")
    except (DemoError, ApiError) as exc:
        return ctx.reporter.fail(ctx.step, exc)

    results, first_error = [], None
    for rec in targets:
        done = True
        try:
            ctx.client.delete_session(rec.session_id)
            outcome = "revoked"
        except ApiNotFound:  # includes Enable Banking's "already gone" error codes
            outcome = "already closed"
        except ApiError as exc:
            done, outcome, first_error = False, f"failed: {exc.message}", first_error or exc
        if done:
            ctx.store.mark_revoked(rec)
            ctx.reporter.ok(Step.REVOKE, f"{rec.bank_name} session {rec.short_id}: {outcome}")
        results.append({"bank": rec.bank_name, "session": rec.short_id, "result": outcome})
    summary = {"revoked": results}
    if first_error:
        return ctx.reporter.fail(Step.REVOKE, first_error, summary=summary)
    return CommandResult(ok=True, summary=summary)


def sessions_prune(ctx: Context, args: argparse.Namespace) -> CommandResult:
    """Delete local records that are revoked or expired; no API calls."""
    ctx.step = Step.SESSIONS
    removed = ctx.store.remove(lambda r: r.local_status(ctx.now) != "active")
    cached = HistoryCache(ctx.cfg.state_dir / "cache").prune(CACHE_RETENTION, ctx.now)
    ctx.reporter.ok(Step.SESSIONS, f"removed {removed} revoked or expired session(s), {cached} old cache file(s)")
    return CommandResult(ok=True, summary={"removed": removed, "cache_files_removed": cached})


def _by_bank(accounts: list[AccountSpec]) -> dict[tuple[str, str], list[AccountSpec]]:
    groups: dict[tuple[str, str], list[AccountSpec]] = {}
    for a in accounts:
        groups.setdefault((a.bank_name.casefold(), a.bank_country), []).append(a)
    return groups


def _status_row(account: AccountSpec, rec: SessionRecord | None, status: str) -> dict:
    return {"account": account.name, "bank": account.bank_name, "iban_masked": iban_mod.mask(account.iban),
            "status": status, "valid_until": rec.valid_until if rec else None}


def _authorize_status(ctx: Context, targets: list[AccountSpec]) -> tuple[list[dict], bool]:
    """authorized / expiring (< 7 days) / consent_required / unknown, from the store + one GET /sessions per bank."""
    ctx.step = Step.SESSIONS
    rows = []
    for account in targets:
        rec = ctx.store.for_account(ctx.cfg.app_id, account)
        try:
            status = ("authorized" if session_state(ctx, rec, account.iban, RENEW_WITHIN)
                      else "expiring" if session_state(ctx, rec, account.iban) else "consent_required")
        except ApiError as exc:
            status = f"unknown ({exc.status or exc.message})"
        rows.append(_status_row(account, rec, status))
    ctx.reporter.ok(Step.SESSIONS, f"{len(rows)} account(s)")
    return rows, all(r["status"] in ("authorized", "expiring") for r in rows)


def _authorize_bank(ctx: Context, group: list[AccountSpec], args: argparse.Namespace) -> tuple[list[dict], bool]:
    """One consent for this bank's accounts, unless the stored one covers them for more than RENEW_WITHIN."""
    rec = ctx.store.for_bank(ctx.cfg.app_id, group[0].bank_name, group[0].bank_country)
    if not args.fresh and rec and rec.valid_at(ctx.now, RENEW_WITHIN) and all(rec.account_uid(a.iban) for a in group):
        ctx.reporter.ok(Step.CONSENT, f"{rec.bank_name}: consent valid until {rec.valid_until}, skipped")
        return [_status_row(a, rec, "authorized") for a in group], True
    try:
        rec, _ = consent_bank(ctx, group[0], args.consent_days or "max")
    except (DemoError, ApiError) as exc:
        ctx.reporter.fail(ctx.step, exc)
        return [_status_row(a, None, "failed") for a in group], False
    rows = []
    for a in group:
        covered = rec.account_uid(a.iban) is not None
        rows.append(_status_row(a, rec, "authorized" if covered else "missing"))
        if not covered:
            ctx.reporter.notice(f"{a.name} {iban_mod.mask(a.iban)} is not in the {a.bank_name} consent: select it "
                                "at the bank, or link it in the Enable Banking control panel, then rerun "
                                f"psd2-demo authorize --account {a.name} --fresh")
    return rows, all(r["status"] == "authorized" for r in rows)


def authorize(ctx: Context, args: argparse.Namespace) -> CommandResult:
    """One interactive consent per bank, covering every selected account there, for the bank's maximum validity
    (or --consent-days). --status only reports, starting no consent."""
    try:
        targets = ctx.cfg.select(args.account)
    except (DemoError, ApiError) as exc:
        return ctx.reporter.fail(ctx.step, exc)
    if not targets:
        return ctx.reporter.fail(ctx.step, UsageError(NO_ACCOUNTS))
    if args.status:
        rows, ok = _authorize_status(ctx, targets)
    else:
        rows, ok = [], True
        for group in _by_bank(targets).values():
            group_rows, group_ok = _authorize_bank(ctx, group, args)
            rows += group_rows
            ok = ok and group_ok
    return CommandResult(ok=ok, failed_step=None if ok else ctx.step, exit_code=EXIT_OK if ok else EXIT_FAIL,
                         summary={"accounts": rows}, render=lambda: render_authorize(rows))


def history(args: argparse.Namespace) -> int:
    """Non-interactive history of the configured accounts as JSON (stdout, or --output). Returns the exit code:
    0 all accounts ok, 3 some, 1 none; 2 config/usage error (no document)."""
    try:
        doc = fetch_history(args.config, accounts=args.account, days=args.days, date_from=args.date_from,
                            date_to=args.date_to, include=args.include, max_age=args.max_age,
                            use_cache=not args.no_cache, progress=sys.stderr if args.verbose else None)
    except ConfigError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return EXIT_CONFIG
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        if note := _outside_exports_note(args.output):
            print(note, file=sys.stderr)
        try:
            write_text_atomic(args.output, text)
        except OSError as exc:
            print(f"error: cannot write {args.output}: {exc.strerror or exc}", file=sys.stderr)
            return EXIT_FAIL
    else:
        sys.stdout.write(text)
    ok = doc["summary"]["accounts"]["ok"]
    return EXIT_OK if ok == len(doc["accounts"]) else EXIT_FAIL if ok == 0 else EXIT_PARTIAL


SESSION_ACTIONS = {"list": sessions_list, "revoke": sessions_revoke, "prune": sessions_prune}


def sessions(ctx: Context, args: argparse.Namespace) -> CommandResult:
    return SESSION_ACTIONS[args.action or "list"](ctx, args)  # bare `sessions` lists


COMMANDS: dict[str, Callable[[Context, argparse.Namespace], CommandResult]] = {
    "check": check,
    "run": run,
    "banks": banks,
    "balances": balances,
    "accounts": accounts,
    "sessions": sessions,
    "authorize": authorize,
}
