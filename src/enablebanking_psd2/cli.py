"""Command-line entry point: ``psd2-demo <command>`` (default ``run``)."""

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from enablebanking_psd2 import __version__
from enablebanking_psd2 import onboarding
from enablebanking_psd2.commands import COMMANDS, history
from enablebanking_psd2.config import ACCOUNT_KEYS, FIELDS, Config, load_config
from enablebanking_psd2.daterange import iso_date
from enablebanking_psd2.bootstrap import load_signer, make_client
from enablebanking_psd2.eb.auth import JwtSigner
from enablebanking_psd2.errors import ConfigError
from enablebanking_psd2.flow import EXIT_INTERRUPT, CommandResult, ConsentHooks, Context, Step, StepReporter, default_capture
from enablebanking_psd2.log import set_own_ibans, setup_logging
from enablebanking_psd2.session_store import SessionStore

def _common(suppress: bool = False) -> argparse.ArgumentParser:
    """Options every command takes. ``suppress``: for nested subcommands, so values given before the
    subcommand aren't reset to defaults by it."""
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS if suppress else None)
    common.add_argument("--config", type=Path, help="TOML config file (default ./config.toml, else the per-user config)")
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


def _period(title: str) -> argparse.ArgumentParser:
    period = argparse.ArgumentParser(add_help=False)
    g = period.add_argument_group(title)
    x = g.add_mutually_exclusive_group()
    x.add_argument("--days", type=int, help="the last N days")
    x.add_argument("--from", dest="date_from", type=iso_date, metavar="YYYY-MM-DD")
    g.add_argument("--to", dest="date_to", type=iso_date, metavar="YYYY-MM-DD", help="default: today")
    return period


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

    period = _period("period (default: last 10 days)")
    return {"common": common, "account": account, "consent": consent, "period": period}


COMMAND_NAMES = ("enablebanking", "enablebanking-psd2", "psd2-demo")


def _prog() -> str:
    """The name the user typed, when it is one of ours (else the canonical short name, e.g. under pytest)."""
    name = Path(sys.argv[0]).name if sys.argv else ""
    return name if name in COMMAND_NAMES else COMMAND_NAMES[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=_prog(),
        description="PSD2 account access to your own bank accounts via Enable Banking.",
        epilog="Examples:\n  enablebanking check\n  enablebanking run\n  enablebanking transactions --days 30 --account main\n"
               "  enablebanking run --from 2026-10-01 --to 2026-10-15 --export exports/oct.json",
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
    banks.add_argument("--country", help="country code (default: [enablebanking] country, else BE)")
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
    auth = sub.add_parser("authorize", parents=[p["common"], p["consent"]], formatter_class=fmt,
                          help="consent once per bank for unattended use (history); --status to check",
                          epilog="Examples:\n  psd2-demo authorize --status\n  psd2-demo authorize\n"
                                 "  psd2-demo authorize --account main --fresh --consent-days 30")
    who = auth.add_mutually_exclusive_group()
    who.add_argument("--account", action="append", metavar="NAME", help="this account (repeatable; default: all)")
    who.add_argument("--all", action="store_true", help="every configured account (the default)")
    auth.add_argument("--status", action="store_true",
                      help="show authorized / expiring / consent_required per account; starts no consent")
    auth.set_defaults(cmd="authorize")
    hist = sub.add_parser("history", parents=[_period("period (default: [history] days, else 30)")],
                          formatter_class=fmt,
                          help="non-interactive history of all configured accounts as JSON (needs authorize)",
                          epilog="Exit codes: 0 all accounts ok, 3 some, 1 none, 2 config/usage error.\n"
                                 "Examples:\n  psd2-demo history\n  psd2-demo history --days 30 --include balances,details "
                                 "--output exports/history.json\n  psd2-demo history --account main --no-cache")
    hist.add_argument("--config", type=Path, help="TOML config file (default: ./config.toml, else the per-user config)")
    hist.add_argument("--account", action="append", metavar="NAME", help="this account (repeatable; default: all)")
    hist.add_argument("--include", metavar="PARTS",
                      help="comma-separated: balances, details, raw; 'none' for transactions only "
                           "(default: [history] include)")
    dest = hist.add_mutually_exclusive_group()
    dest.add_argument("--output", type=Path, metavar="FILE", help="write the JSON here (atomic, mode 0600)")
    dest.add_argument("--save", action="store_true",
                      help="write the JSON to <export dir>/history.json and print only that path")
    hist.add_argument("--max-age", type=int, metavar="MIN", help="use cached results up to MIN minutes old "
                                                                  "(default: [history] max_age)")
    hist.add_argument("--no-cache", action="store_true", help="always call the API; don't read or write the cache")
    hist.add_argument("--verbose", "-v", action="store_true", help="per-account progress and HTTP debug on stderr")
    hist.add_argument("--json", action="store_true", help="accepted for symmetry; output is always JSON")
    hist.set_defaults(cmd="history")
    init = sub.add_parser("init", formatter_class=fmt,
                          help="guided first-time setup: writes the config file and runs check",
                          epilog="Writes the per-user config (or --config PATH), mode 600; nothing is written until the end.")
    init.add_argument("--config", type=Path, help="write the config here (default: the per-user config file)")
    init.add_argument("--force", action="store_true", help="overwrite an existing config without asking")
    init.set_defaults(cmd="init")
    mcp = sub.add_parser("mcp", formatter_class=fmt,
                         help="run the read-only MCP server on stdio (needs: pip install 'enablebanking-psd2[mcp]')",
                         epilog="Tools: get_history, consent_status. There is no tool that starts a consent.")
    mcp.add_argument("--config", type=Path, help="TOML config file (default: ./config.toml, else the per-user config)")
    mcp.add_argument("--verbose", "-v", action="store_true", help="log tool calls and HTTP debug to stderr")
    mcp.set_defaults(cmd="mcp")
    parser.command_names = set(sub.choices)  # names and aliases, for the default-command rule
    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = build_parser()
    if not argv or argv[0] not in parser.command_names and argv[0] not in ("-h", "--help", "--version"):
        argv = ["run", *argv]
    return parser.parse_args(argv)


def _load(args: argparse.Namespace) -> tuple[Config, JwtSigner]:
    overrides = {k: getattr(args, k, None) for k in (*FIELDS, *ACCOUNT_KEYS)}
    cfg = load_config(args.config, os.environ, overrides)
    return cfg, load_signer(cfg)


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
        if args.cmd == "history":
            return history(args)
        if args.cmd == "mcp":
            from enablebanking_psd2 import mcp_server  # lazy: the SDK is an optional extra

            return mcp_server.main(args.config, args.verbose)
        if args.cmd == "init":
            return onboarding.run(args, os.environ)
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
