"""Commands: each turns parsed arguments into a CommandResult (summary for --json, render for humans).

Pattern: one ``try`` around the steps, ``except (DemoError, ApiError)`` → ``reporter.fail(ctx.step, exc)``;
``ctx.step`` is set by whichever step runs (it starts at CONFIG, which covers account and range resolution).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable

from psd2demo import iban as iban_mod
from psd2demo.consent import prompt_stderr
from psd2demo.daterange import resolve_range
from psd2demo.eb.errors import ApiError, ApiNotFound
from psd2demo.errors import DemoError, UsageError
from psd2demo.flow import (DEFAULT_LOOKBACK_DAYS, AccountSession, CommandResult, Context, Step, bank_for,
                           ensure_session, run_transactions, totals_json)
from psd2demo.output import (balance_label, export_balances, export_json, flatten, render_accounts,
                             render_balances, render_banks, render_details, render_sessions, render_summary,
                             render_transactions)
from psd2demo.session_store import SessionRecord

EXPORTS_DIR = Path("exports")


def _account_session(ctx: Context, args: argparse.Namespace) -> AccountSession:
    """The selected account with a usable session (reused or newly consented)."""
    return ensure_session(ctx, ctx.cfg.account(args.account), fresh=args.fresh)


def _meta(s: AccountSession) -> dict:
    """Fields every account command's summary and export start with."""
    return {"account": s.account.name, "bank": s.record.bank_name, "country": s.record.bank_country,
            "iban_masked": iban_mod.mask(s.account.iban)}


def _export(ctx: Context, path: Path, write: Callable[[Path], None], result: CommandResult) -> CommandResult:
    """EXPORT step: write the file; a failure fails the command but keeps its rendered output."""
    ctx.step = Step.EXPORT
    if EXPORTS_DIR.resolve() not in path.resolve().parents:
        ctx.reporter.notice(f"note: {path} is outside exports/, so it is not git-ignored; "
                            "it contains personal financial data")
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
    summary = {**meta, "balances": [{"type": b.balance_type, "type_label": balance_label(b.balance_type) or None,
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
            raw = ctx.client.get_account_details(session.uid).raw
            details = {k: iban_mod.mask_others(v, session.account.iban) for k, v in flatten(raw).items()}
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
    ctx.reporter.ok(Step.SESSIONS, f"removed {removed} revoked or expired session(s)")
    return CommandResult(ok=True, summary={"removed": removed})


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
}
