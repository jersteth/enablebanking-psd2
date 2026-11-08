# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project
`enablebanking-psd2`: a test client for PSD2 account access (AIS) to Belgian personal bank accounts, using the Enable Banking API (enablebanking.com) as the aggregator. See `./specs/product.md`.

## Commands
```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # setup (no uv on this machine)
.venv/bin/pytest                                              # all tests (offline, respx-mocked HTTP)
.venv/bin/pytest tests/test_client.py::test_pagination        # single test
.venv/bin/psd2-demo check | run/transactions | balances | accounts | banks | sessions [revoke|prune]
.venv/bin/psd2-demo authorize [--status] | history [--days N] [--include balances,details,raw] [--output F]
.venv/bin/python tests/history_docs.py                       # regenerate schemas/account-history.v1.example.json
```
Config: `config.toml` (git-ignored; template `config.example.toml`). Precedence: CLI flag > `EB_*` env > TOML > defaults. Accounts are `[accounts.<name>]` tables (`--account`); the 002 `[demo]` bank/iban form becomes account `demo`; `--bank`+`--iban` is an ad-hoc account that overrides the selection. Top-level TOML keys (`default_account`) must precede the first table.

## Architecture
- `src/psd2demo/eb/`: reusable Enable Banking client with no CLI/IO. `auth.py` (RS256 JWT, `kid`=app id, cached), `client.py` (httpx; GET retries on 429/5xx/network, non-GET only on 429 because the `/sessions` code is single-use; `iter_transactions` follows `continuation_key` with a loop guard; `request_count`), `models.py`, `errors.py` (`ApiError` hierarchy with hints; `hint_override` for caller context).
- `cli.py` parses (parent parsers: common/account/consent/period), loads config, builds the `flow.Context`, dispatches `commands.COMMANDS[args.cmd]`, then prints `CommandResult.render()` or the `--json` envelope `{ok, failed_step, steps, summary}`.
- `commands.py`: one function per command returning `CommandResult(summary, render)`. Each wraps its steps in one `except (DemoError, ApiError)` → `reporter.fail(ctx.step, exc)`; `ctx.step` is set by whichever step runs. Exit codes come from the error type: `ConfigError`/`UsageError` → 2, others → 1.
- `flow.py`: `ensure_session(ctx, account, interactive, fresh, consent_days)` is the only consent path (BANK + all-or-none PSU headers → reuse the bank's stored session or a new consent → ACCOUNT). A stored record's `BankMeta` lets reuse skip `GET /aspsps`. `interactive=False` raises `ConsentRequired` (for 004). Also `run_transactions`, `StepReporter`, `Context`.
- `session_store.py`: `.psd2demo/session.json` schema v2, one `SessionRecord` per (app, bank, country) listing every authorized account (uid, IBAN SHA-256, masked display data, never a full IBAN). v1 files are migrated on read and rewritten only on the next write; unreadable files are kept as `.bak`.
- `config.py` (`AccountSpec`, `HistorySettings`, `Config.account()/select()`), `daterange.py` (`resolve_range`, `UsageError` → exit 2), `consent.py` (state, capture: paste default / loopback listener), `output.py` (`_table` and renderers, JSON exports 0600).
- `history.py` (`fetch_history` = Python API, `build_document`): non-interactive, per-account isolated; uses `ensure_session(interactive=False)` and never writes the session store. `contract.py` owns the JSON contract (schema v1, `schemas/account-history.v1.json`); **a breaking change means schema v2 and a new schema file**. `cache.py` caches per (account, range, include). `commands.history` maps results to exit codes 0/3/1 (2 = config). `bootstrap.py` builds the signed client for CLI and API; `fileio.write_text_atomic` is the one way to write personal data (atomic, 0600).
- `.claude/skills/bank-account-history/SKILL.md`: Claude Code skill wrapping `history`; `tests/test_skill.py` checks every command in it parses.
- Never log the JWT, the authorization code/state, or non-target IBANs. `log.RedactingFilter` enforces this; `log.set_own_ibans` (called once in `cli`) keeps the configured accounts readable.
- Testing: `tests/conftest.py` has `FakeClient`, `make_cfg`, `make_ctx(stored=True)`, `session_record`, `cmd_args` (real CLI namespace), `PasteStdin`, and the example IBAN constants. Resolve `sys.stderr`/`sys.stdin` at call time, not as default arguments, so pytest capture and monkeypatch work.

## Development Process

### Steering Documents
The `./specs` folder contains steering documents for the project:
- `./specs/product.md` - Business-level overview of the application
- `./specs/index.md` - Index of all feature specs

### Spec-Driven Development
For each new major feature, create a new folder in `./specs` with sequential numbering (e.g., `001-feature-name`) containing:
- `requirements.md` - Detailed description of the feature requirements
- `design.md` - Detailed design of the feature
- `tasks.md` - List of tasks, grouped by phases, to implement the feature

### Documentation
When a user request is completed, create or update documentation in `./documentation` to reflect the changes:
1. Keep one file per functional or technical domain area
2. **Always update `./documentation/index.md`** when adding new documentation files
3. Before finishing any task that adds or modifies features, check if documentation needs updating
4. Reference `./documentation/index.md` to see existing documentation topics
