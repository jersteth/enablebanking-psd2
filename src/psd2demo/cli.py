"""Command-line entry point: ``psd2-demo [run|check]``."""

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from psd2demo import __version__
from psd2demo import iban as iban_mod
from psd2demo.config import FIELDS, Config, load_config
from psd2demo.eb.auth import JwtSigner
from psd2demo.eb.client import EnableBankingClient
from psd2demo.errors import ConfigError, DemoError
from psd2demo.flow import EXIT_INTERRUPT, RunResult, Step, StepReporter, run_check, run_demo
from psd2demo.log import http_event_hooks, setup_logging
from psd2demo.output import export_json, render_summary, render_transactions

COMMANDS = ("run", "check")


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", type=Path, help="TOML config file (default ./config.toml)")
    p.add_argument("--verbose", "-v", action="store_true", help="debug HTTP logging (redacted)")
    p.add_argument("--json", action="store_true", help="print a machine-readable result to stdout")
    g = p.add_argument_group("config overrides")
    g.add_argument("--app-id")
    g.add_argument("--key", dest="key_path", help="private key PEM path")
    g.add_argument("--redirect-url")
    g.add_argument("--bank", dest="bank_name")
    g.add_argument("--country", dest="bank_country")
    g.add_argument("--iban")
    g.add_argument("--capture", dest="capture_mode", choices=["auto", "paste", "listener"])
    g.add_argument("--consent-days", type=int)
    g.add_argument("--callback-timeout", type=int)
    g.add_argument("--psu-ip", dest="psu_ip_address")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psd2-demo",
        description="End-to-end PSD2 demo: last 10 days of transactions of one account via Enable Banking.",
        epilog="Examples:\n  psd2-demo check\n  psd2-demo run\n  psd2-demo run --fresh --export exports/run.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="consent (or reuse) and show the last 10 days of transactions (default)")
    _add_common(run)
    run.add_argument("--fresh", action="store_true", help="ignore the stored session; force a new consent")
    run.add_argument("--export", type=Path, metavar="FILE", help="write transactions and raw API pages as JSON")
    check = sub.add_parser("check", help="validate config and key, call GET /aspsps; no consent")
    _add_common(check)
    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    if not argv or argv[0] not in COMMANDS and argv[0] not in ("-h", "--help", "--version"):
        argv = ["run", *argv]
    return build_parser().parse_args(argv)


def make_client(cfg: Config, signer: JwtSigner) -> EnableBankingClient:
    psu = {"Psu-User-Agent": f"psd2-demo/{__version__}"}
    if cfg.psu_ip_address:
        psu["Psu-Ip-Address"] = cfg.psu_ip_address
    return EnableBankingClient(signer, psu_headers=psu, event_hooks=http_event_hooks())


def _load(args: argparse.Namespace) -> tuple[Config, JwtSigner]:
    overrides = {k: getattr(args, k) for k in FIELDS}
    cfg = load_config(args.config, os.environ, overrides)
    try:
        signer = JwtSigner(cfg.app_id, cfg.key_path.read_bytes())
    except Exception as exc:  # bad PEM, encrypted key, unsupported key type
        raise ConfigError([f"private key {cfg.key_path} cannot be loaded: {exc}"]) from exc
    return cfg, signer


def _execute(args: argparse.Namespace, reporter: StepReporter) -> RunResult:
    try:
        cfg, signer = _load(args)
    except ConfigError as exc:
        return reporter.fail(Step.CONFIG, exc)
    setup_logging(args.verbose, cfg.iban)
    reporter.ok(Step.CONFIG, f"app {cfg.app_id}, account {iban_mod.mask(cfg.iban)}, capture={cfg.capture_mode}")
    with make_client(cfg, signer) as client:
        if args.command == "check":
            return run_check(cfg, client, reporter)
        return run_demo(cfg, client, reporter, fresh=args.fresh)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    setup_logging(args.verbose)
    reporter = StepReporter(quiet=args.json)
    try:
        result = _execute(args, reporter)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return EXIT_INTERRUPT
    if args.command == "run" and result.ok:
        if args.export:
            try:
                export_json(args.export, result.summary, result.txns, result.raw_pages)
                reporter.ok(Step.EXPORT, str(args.export))
            except OSError as exc:
                failed = reporter.fail(Step.EXPORT, DemoError(f"cannot write {args.export}: {exc.strerror or exc}"))
                result = replace(result, ok=False, failed_step=failed.failed_step, exit_code=failed.exit_code)
        if not args.json:
            print()
            print(render_transactions(result.txns))
            print()
            print(render_summary(result.totals))
    if args.json:
        print(json.dumps({
            "ok": result.ok,
            "failed_step": result.failed_step.value if result.failed_step else None,
            "steps": [vars(s) for s in reporter.steps],
            "summary": result.summary,
        }, indent=2, default=str))
    return result.exit_code
