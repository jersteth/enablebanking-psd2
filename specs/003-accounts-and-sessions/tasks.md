# 003 – Accounts and Sessions: Tasks

Refs: `requirements.md` (R1–R7), `design.md`. Mark tasks `[x]` when they are implemented and their tests pass. Branch: `feature/003-accounts-and-sessions`. The 002 test suite must stay green after every task (regression gate).

## Phase 1 – Accounts Config, Date Ranges, Session Store v2
**Goal**: the multi-account config, range resolution and v2 store exist offline, and the existing `run` behaves exactly as in 002.
**Verify (user)**:
1. `pytest` passes.
2. With your unchanged `config.toml` (`[demo]`), run `psd2-demo run`. It reuses the existing session, which is migrated in memory with no new consent, and shows the transactions.
3. `.psd2demo/session.json` is still schema v1 (reads never write).

- [ ] 1.1 `config.py`:
  - `AccountSpec`, `HistorySettings`, and the `Config.accounts`/`default_account`/`history` fields
  - the account merge (`[accounts.*]` → legacy `[demo]` → env account → CLI account)
  - `Config.account(name)`, `Config.select(names)`
  - duplicate-IBAN and half-specified-flag errors; `[history]` parsing and env vars
  - `FIELDS` reduced to app-level keys; an account is no longer required globally

  Test: `test_config.py` (extend: every source, precedence, selection rule, errors, history).
- [ ] 1.2 Temporary adapter: `flow.run_demo`/`run_check` read `cfg.account()` instead of `cfg.bank_name`/`cfg.iban`, so 002 behaviour is kept until Phase 2. Test: the existing `test_flow_run.py`/`test_flow_check.py` pass, with fixtures switched to `make_cfg` building `accounts`.
- [ ] 1.3 `daterange.py`: `UsageError`, `resolve_range`, and the argparse type `iso_date`. Test: `test_daterange.py` (days, from/to, defaults, from > to, future to, days < 1, invalid date string).
- [ ] 1.4 `session_store.py` v2:
  - `StoredAccount`, `BankMeta`, `SessionRecord` (`account_uid`, `valid_at`)
  - `SessionStore.records/for_bank/for_account/upsert/update/remove`
  - v1 → v2 migration on read (no write); write as v2; unreadable file → a `.bak` on the first write; atomic, 0600/0700

  Test: `test_session_store.py` (rewrite: round trip, migration, `.bak`, case-insensitive bank, remove, permissions).
- [ ] 1.5 Adapt the `run_demo` CONSENT/ACCOUNT steps to the v2 store (`for_bank` + `account_uid`; `upsert` storing all session accounts + `BankMeta`). Test: `test_flow_run.py` (reuse via a migrated v1 file; new consent writes v2 with all accounts).

## Phase 2 – Shared Session Step, Command Layer, `run`/`check` on It
**Goal**: one `ensure_session` path and a `CommandResult` command layer. `run`/`transactions` gains date ranges and `--account`.
**Verify (user)**:
1. `psd2-demo check` and `psd2-demo run` behave as before.
2. `psd2-demo transactions --days 30` shows 30 days (range in the TRANSACTIONS line).
3. `psd2-demo run --from 2026-10-01 --to 2026-10-15` works.
4. `psd2-demo run --days 5 --from 2026-10-01` exits 2.

- [ ] 2.1 `eb/models.py` / `eb/client.py`:
  - `Aspsp` gains `bic` and `beta`
  - new `Balance`, `AccountDetails`
  - new client calls `get_balances`, `get_account_details`, `delete_session` (non-GET retry policy), and `request_count`
  - `ApiError` keeps `code`, and `describe()` shows it when it differs from the status (V14)

  Test: `test_models.py`, `test_client.py`, `test_errors.py` (extend). Fixtures: `balances.json`, `details.json`.
- [ ] 2.2 `flow.py`:
  - `Context`, `AccountSession`, `ConsentRequired`, and the new `Step` members
  - `ensure_session(ctx, account, interactive, fresh, consent_days)` per the design: BANK + PSU, reuse rules, a new consent storing all accounts + `BankMeta`, `consent_days=None` → the bank maximum, the save-failure warning, ACCOUNT match
  - `run_transactions(ctx, a, from, to)` with the 400/422 range hint
  - remove the old `run_demo` internals

  Test: `test_flow_session.py` (reuse; reuse for a second account at the same bank; stale → new consent; 401 on `get_session` → stale (V17); `interactive=False` → `ConsentRequired`; consent-days clamp; save failure), `test_flow_run.py` (adapted).
- [ ] 2.3 `commands.py`: `CommandResult`, `COMMANDS`, plus `check` and `run`/`transactions` (range via `resolve_range`, export with the V12 notice for paths outside `exports/`). Test: `test_commands_run.py` (summary contents, range in detail, export notice).
- [ ] 2.4 `cli.py` rewrite:
  - parent parsers (`common`, `account`, `consent`, `range`)
  - subcommands `run` (alias `transactions`) and `check`; default `run`
  - `main` dispatches through `COMMANDS`, uses the `{ok, failed_step, steps, summary}` envelope, and `render()`
  - `KeyboardInterrupt` around the whole body → 130 (V13); `UsageError` → 2

  Test: `test_cli.py` (adapted: alias, range mutual exclusion → 2, JSON purity, Ctrl+C → 130 via a monkeypatched command).
- [ ] 2.5 `output.py`: extract the `_table(columns, rows, width, flex_col)` helper and refactor `render_transactions` onto it. Test: the golden table in `test_output.py` is unchanged.

## Phase 3 – `banks`, `balances`, `accounts`
**Goal**: the new read commands on the shared session step.
**Verify (user)**:
1. `psd2-demo banks --filter kbc` lists matches with BIC, max validity and required headers.
2. `psd2-demo balances` shows labelled balances.
3. `psd2-demo accounts --details` lists the session's accounts (others masked) and the flattened details.
4. Each command with `--json` gives valid JSON.

- [ ] 3.1 `banks` command + `render_banks` (sorted, filter, beta flag, max validity in days, required PSU headers; no account needed). Test: `test_commands_banks.py` (filter, sort, render golden, JSON summary, works without accounts).
- [ ] 3.2 `balances` command + `render_balances` (type labels, unknown code raw, signed amounts, zero balances = pass, `--export`). Test: `test_commands_balances.py`.
- [ ] 3.3 `accounts` command + `render_accounts` + `render_details` (selected account unmasked; others masked; `--details` flattening `a.b`; IBAN-like values masked except the selected account's). Test: `test_commands_accounts.py`.
- [ ] 3.4 CLI subparsers for `banks`, `balances`, `accounts` with help examples. Test: `test_cli.py` (help, dispatch, `--account` selection, unknown account → exit 2).
- [ ] 3.5 Multi-account end-to-end test: two accounts at one bank plus one at another, through the CLI with a mocked API. One consent covers both same-bank accounts, and the other bank triggers its own consent. Test: `test_cli_multi_account.py`.

## Phase 4 – `sessions`, Folded-in 002 Items, Docs
**Goal**: consent lifecycle management, the remaining 002 verify items, and updated documentation.
**Verify (user)**:
1. `psd2-demo sessions` lists your bank session with its live status.
2. `psd2-demo sessions revoke --account <name>` revokes it.
3. The next `psd2-demo run` asks for a new consent.
4. `psd2-demo sessions prune` removes the revoked record.

- [ ] 4.1 `sessions` list command + `render_sessions` (live status per record, `--offline`, masked configured IBANs). Test: `test_commands_sessions.py`.
- [ ] 4.2 `sessions revoke` (`--account`/`--bank`/`--all`, the confirmation prompt via `prompt_stderr` unless `--yes`, 404/already-closed = success, failure isolation, exit 1 on any failure, `update(status=revoked)`). Test: `test_commands_sessions.py` (each case; no DELETE when the confirmation is declined).
- [ ] 4.3 `sessions prune` (revoked or expired records). Test: `test_commands_sessions.py`.
- [ ] 4.4 CLI nested subparser `sessions {list,revoke,prune}` (`list` is the default). Test: `test_cli.py`.
- [ ] 4.5 V17 tests:
  - verbose HTTP hook: method, URL, status, truncation and redaction of a real httpx response in `test_log.py`
  - `GET /sessions/{id}` sends no PSU headers, in `test_client.py`
- [ ] 4.6 Docs:
  - `config.example.toml`: `[accounts.*]`, `default_account`, `[history]` keys (documented as used by 004)
  - `documentation/enablebanking-setup.md`: multiple accounts, the new commands, date ranges, sessions/revoke, and troubleshooting rows for `several accounts configured` and unknown account
  - `README.md`: the command list
  - `CLAUDE.md`: architecture (`commands.py`, `ensure_session`, store v2)
  - `documentation/index.md`
  - record the V16 minor interface deviations in `002/tasks.md`
- [ ] 4.7 Final check: full `pytest`; `grep` for secrets and real IBANs; `.gitignore` covers `.psd2demo/` (including `session.json.bak`) and `exports/`; manual production check per the requirements.
