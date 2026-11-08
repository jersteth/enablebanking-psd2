"""Command-line entry point: ``psd2-demo <command>`` (default ``run``)."""

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from psd2demo import __version__
from psd2demo.commands import COMMANDS
from psd2demo.config import ACCOUNT_KEYS, FIELDS, Config, load_config
from psd2demo.daterange import iso_date
from psd2demo.eb.auth import JwtSigner
from psd2demo.eb.client import EnableBankingClient
from psd2demo.errors import ConfigError
from psd2demo.flow import EXIT_INTERRUPT, CommandResult, ConsentHooks, Context, Step, StepReporter, default_capture
from psd2demo.log import http_event_hooks, set_own_ibans, setup_logging
from psd2demo.session_store import SessionStore

def _common(suppress: bool = False) -> argparse.ArgumentParser:
    """Options every command takes. ``suppress``: for nested subcommands, so values given before the
    subcommand aren't reset to defaults by it."""
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS if suppress else None)
    common.add_argument("--config", type=Path, help="TOML config file (default ./config.toml)")
    common.add_argument("--verbose", "-v", action="store_true", default=False if not suppress else argparse.SUPPRESS,
                        help="debug HTTP logging (redacted)")
    common.add_argument("--json", action="store_true", default=False if not suppress else argparse.SUPPRESS,
                        help="print a machine-readable result to stdout")
    g = common.add_argument_group("app overrides")
    g.add_argument("--app-id")
    g.add_argument("--key", dest="key_path", help="private key PEM path")
    g.add_argument("--redirect-url")
    g.add_argument("--psu-ip", dest="psu_ip_address", help="your public IP, sent as Psu-Ip-Address")
    return common


def _parents() -> dict[str, argparse.ArgumentParser]:
    common = _common()
    account = argparse.ArgumentParser(add_help=False)
    g = account.add_argument_group("account")
    g.add_argument("--account", help="configured account name ([accounts.<name>])")
    g.add_argument("--bank", dest="bank_name", help="ad-hoc account: bank name (with --iban)")
    g.add_argument("--iban", help="ad-hoc account: IBAN (with --bank)")
    g.add_argument("--country", dest="bank_country", help="ad-hoc account: bank country (default BE)")

    consent = argparse.ArgumentParser(add_help=False)
    g = consent.add_argument_group("consent")
    g.add_argument("--fresh", action="store_true", help="ignore the stored session; force a new consent")
    g.add_argument("--capture", dest="capture_mode", choices=["auto", "paste", "listener"])
    g.add_argument("--consent-days", type=int)
    g.add_argument("--callback-timeout", type=int)

    period = argparse.ArgumentParser(add_help=False)
    g = period.add_argument_group("period (default: last 10 days)")
    x = g.add_mutually_exclusive_group()
    x.add_argument("--days", type=int, help="the last N days")
    x.add_argument("--from", dest="date_from", type=iso_date, metavar="YYYY-MM-DD")
    g.add_argument("--to", dest="date_to", type=iso_date, metavar="YYYY-MM-DD", help="default: today")
    return {"common": common, "account": account, "consent": consent, "period": period}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psd2-demo",
        description="PSD2 account access for Belgian personal accounts via Enable Banking.",
        epilog="Examples:\n  psd2-demo check\n  psd2-demo run\n  psd2-demo transactions --days 30 --account main\n"
               "  psd2-demo run --from 2026-10-01 --to 2026-10-15 --export exports/oct.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    p = _parents()
    fmt = argparse.RawDescriptionHelpFormatter

    run = sub.add_parser("run", aliases=["transactions"], parents=[p["common"], p["account"], p["consent"], p["period"]],
                         help="transactions of one account (default command)", formatter_class=fmt,
                         epilog="Examples:\n  psd2-demo run --days 30\n  psd2-demo transactions --account savings "
                                "--from 2026-10-01\n  psd2-demo run --fresh --export exports/run.json")
    run.add_argument("--export", type=Path, metavar="FILE", help="write transactions and raw API pages as JSON")
    run.set_defaults(cmd="run")

    check = sub.add_parser("check", parents=[p["common"], p["account"]], formatter_class=fmt,
                           help="validate config, key and the account's bank; no consent",
                           epilog="Examples:\n  psd2-demo check\n  psd2-demo check --account savings")
    check.set_defaults(cmd="check")

    banks = sub.add_parser("banks", parents=[p["common"]], formatter_class=fmt,
                           help="list banks: BIC, max consent validity, required PSU headers",
                           epilog="Examples:\n  psd2-demo banks\n  psd2-demo banks --filter kbc --json")
    banks.add_argument("--country", default="BE", help="country code (default BE)")
    banks.add_argument("--filter", metavar="TEXT", help="case-insensitive name substring")
    banks.set_defaults(cmd="banks")

    balances = sub.add_parser("balances", parents=[p["common"], p["account"], p["consent"]], formatter_class=fmt,
                              help="balances of one account",
                              epilog="Examples:\n  psd2-demo balances\n  psd2-demo balances --account savings --json")
    balances.add_argument("--export", type=Path, metavar="FILE", help="write the balances as JSON")
    balances.set_defaults(cmd="balances")

    accounts = sub.add_parser("accounts", parents=[p["common"], p["account"], p["consent"]], formatter_class=fmt,
                              help="accounts covered by the consent; --details for the bank's account data",
                              epilog="Examples:\n  psd2-demo accounts\n  psd2-demo accounts --details")
    accounts.add_argument("--details", action="store_true", help="also fetch the selected account's details")
    accounts.set_defaults(cmd="accounts")

    sessions = sub.add_parser("sessions", parents=[p["common"]], formatter_class=fmt,
                              help="list, revoke or prune stored consents",
                              epilog="Examples:\n  psd2-demo sessions\n  psd2-demo sessions revoke --account main\n"
                                     "  psd2-demo sessions revoke --all --yes\n  psd2-demo sessions prune")
    sessions.add_argument("--offline", action="store_true", help="don't ask Enable Banking for the live status")
    sessions.set_defaults(cmd="sessions")
    actions = sessions.add_subparsers(dest="action", metavar="<action>")
    nested = _common(suppress=True)  # options work before or after the action
    s_list = actions.add_parser("list", parents=[nested], help="stored sessions (default)")
    s_list.add_argument("--offline", action="store_true", default=argparse.SUPPRESS,
                        help="don't ask Enable Banking for the live status")
    revoke = actions.add_parser("revoke", parents=[nested], formatter_class=fmt,
                                help="revoke consents at the bank (DELETE /sessions)",
                                epilog="Examples:\n  psd2-demo sessions revoke --account main\n"
                                       "  psd2-demo sessions revoke --bank KBC\n  psd2-demo sessions revoke --all --yes")
    target = revoke.add_mutually_exclusive_group()
    target.add_argument("--account", help="the session of this account's bank")
    target.add_argument("--bank", help="every stored session of this bank")
    target.add_argument("--all", action="store_true", help="every active stored session (asks to confirm)")
    revoke.add_argument("--yes", action="store_true", help="don't ask for confirmation with --all")
    actions.add_parser("prune", parents=[nested], help="delete revoked or expired local records")
    parser.command_names = set(sub.choices)  # names and aliases, for the default-command rule
    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = build_parser()
    if not argv or argv[0] not in parser.command_names and argv[0] not in ("-h", "--help", "--version"):
        argv = ["run", *argv]
    return parser.parse_args(argv)


def make_client(cfg: Config, signer: JwtSigner) -> EnableBankingClient:
    psu = {"Psu-User-Agent": f"psd2-demo/{__version__}"}
    if cfg.psu_ip_address:
        psu["Psu-Ip-Address"] = cfg.psu_ip_address
    return EnableBankingClient(signer, psu_headers=psu, event_hooks=http_event_hooks())


def _load(args: argparse.Namespace) -> tuple[Config, JwtSigner]:
    overrides = {k: getattr(args, k, None) for k in (*FIELDS, *ACCOUNT_KEYS)}
    cfg = load_config(args.config, os.environ, overrides)
    try:
        signer = JwtSigner(cfg.app_id, cfg.key_path.read_bytes())
    except Exception as exc:  # bad PEM, encrypted key, unsupported key type
        raise ConfigError([f"private key {cfg.key_path} cannot be loaded: {exc}"]) from exc
    return cfg, signer


def _execute(args: argparse.Namespace, reporter: StepReporter) -> CommandResult:
    try:
        cfg, signer = _load(args)
    except ConfigError as exc:
        return reporter.fail(Step.CONFIG, exc)
    set_own_ibans(a.iban for a in cfg.accounts.values())
    reporter.ok(Step.CONFIG, f"app {cfg.app_id}, {len(cfg.accounts)} account(s), capture={cfg.capture_mode}")
    with make_client(cfg, signer) as client:
        ctx = Context(cfg=cfg, client=client, store=SessionStore(cfg.state_dir / "session.json"),
                      reporter=reporter, hooks=ConsentHooks(capture=default_capture),
                      today=date.today(), now=datetime.now(timezone.utc))
        return COMMANDS[args.cmd](ctx, args)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
        setup_logging(args.verbose)
        reporter = StepReporter(quiet=args.json)
        result = _execute(args, reporter)
        if args.json:
            print(json.dumps({
                "ok": result.ok,
                "failed_step": result.failed_step.value if result.failed_step else None,
                "steps": [vars(s) for s in reporter.steps],
                "summary": result.summary,
            }, indent=2, default=str))
        elif result.render:
            print(result.render())
        return result.exit_code
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return EXIT_INTERRUPT
