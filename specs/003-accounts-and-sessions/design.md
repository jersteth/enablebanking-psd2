# 003 – Accounts and Sessions: Design

## Overview
This extends the 002 CLI from one hard-wired account and one stored session to:
- named accounts (`[accounts.<name>]`) with one selected account per command
- a multi-session store with one consent per bank, covering every account authorized at that bank
- a single shared **ensure-session** path (bank → consent or reuse → account) that every account command uses; 004 adds a non-interactive mode on the same function
- new commands `banks`, `balances`, `accounts`, `sessions [list|revoke|prune]`, and date ranges on `run` (alias `transactions`)
- a command layer that returns data plus a renderer, so `cli.main` no longer branches per command

The deferred 002 verify items (V12–V14, V16, V17) are folded in. Section *Folded-in 002 items* lists them.

004's design depends on: `AccountSpec`, `Config.accounts`/`select()`, `SessionStore` v2, `ensure_session(... interactive=False)`, `resolve_range`, `get_balances`/`get_account_details`, and the `CommandResult` pattern.

## Architecture

```
cli.py  ── parse args ── load_config ── signer/client ── COMMANDS[name](ctx) ──► CommandResult
  │                                                                              │
  │  ctx = Context(cfg, client, store, reporter, today, now, hooks, args)         ├─ render() → stdout (human)
  │                                                                              └─ summary  → stdout (--json)
commands.py ── check · banks · run/transactions · balances · accounts · sessions{list,revoke,prune}
  │                 │
  │                 └──► flow.ensure_session(ctx, account, interactive=True, fresh)
  │                          BANK: aspsp lookup (or cached in record) + PSU all-or-none
  │                          CONSENT: reuse bank record │ new consent (start_auth → capture → create_session)
  │                          ACCOUNT: match IBAN in record → uid
  ▼
eb/client.py  (+ get_balances, get_account_details, delete_session)
session_store.py (v2: list of SessionRecord, migration v1→v2)
config.py (AccountSpec, accounts, default_account, [history] parsed for 004)
daterange.py (resolve_range)   output.py (renderers)
```

## Components and Interfaces

### `config.py` (changed)
```python
@dataclass(frozen=True)
class AccountSpec:
    name: str; bank_name: str; bank_country: str; iban: str      # iban normalized + validated

@dataclass(frozen=True)
class HistorySettings:                                           # parsed here, used by 004
    days: int = 30; include: frozenset[str] = frozenset(); max_age: int = 60

@dataclass(frozen=True)
class Config:
    app_id, key_path, redirect_url, capture_mode, consent_days, callback_timeout, psu_ip_address, state_dir
    accounts: dict[str, AccountSpec]          # insertion order = config order
    default_account: str | None
    history: HistorySettings
    def account(self, name: str | None = None) -> AccountSpec   # selection rule, raises ConfigError
    def select(self, names: Sequence[str] | None) -> list[AccountSpec]   # None → all, config order

def load_config(path, env, overrides, *, account_override: AccountSpec | None = None) -> Config
```
- **Account sources, merged in order:**
  1. `[accounts.<name>]` tables (`bank_name`, `bank_country` default `BE`, `iban`)
  2. legacy `[demo]` → account `demo` (when no `accounts.demo` exists)
  3. env `EB_BANK_NAME` + `EB_IBAN` → account `env`
  4. flags `--bank` + `--iban` → account `cli`
- **Overrides:** an env or CLI account becomes the selected account automatically (AC3), overriding `--account`/the default. `--bank` without `--iban` (or the reverse) is a `ConfigError`. `--country` applies to the env/CLI account only.
- **`account(name)` selection rule:** explicit name → `default_account` (top-level TOML key) → the single account → `ConfigError("several accounts configured; pass --account (one of: a, b)")`. Unknown name → `ConfigError` listing the names.
- **Validation:** every account's IBAN; duplicate IBANs across accounts → `ConfigError`.
- **Required fields:** `bank_name`/`iban` are no longer required globally. Commands that need an account call `cfg.account()`. `check` with no account skips the bank match, and `banks`/`sessions` don't need an account.
- **`[history]`:** `days` (int ≥ 1), `include` (list ⊆ `{balances, details, raw}`), `max_age` (int ≥ 0, minutes). Env: `EB_HISTORY_DAYS`, `EB_HISTORY_INCLUDE` (comma-separated), `EB_HISTORY_MAX_AGE`.
- **`FIELDS`:** the table now covers only app-level keys. Account keys are handled by the account merge above.

### `daterange.py` (new)
```python
class UsageError(DemoError): ...                 # exit 2
def resolve_range(today: date, *, days: int | None, date_from: date | None, date_to: date | None,
                  default_days: int) -> tuple[date, date]
```
- Mutually exclusive: `days` vs `date_from`/`date_to` (also enforced by an argparse group).
- `date_to` defaults to `today`, and `date_from` defaults to `date_to - days`.
- `from > to`, a future `to`, or `days < 1` → `UsageError`.
- argparse type `iso_date` parses `YYYY-MM-DD`; invalid input → argparse error (exit 2).

### `session_store.py` (rewritten, schema v2)
```python
@dataclass(frozen=True)
class StoredAccount: uid: str; iban_sha256: str
@dataclass(frozen=True)
class BankMeta: maximum_consent_validity: int | None; required_psu_headers: tuple[str, ...]
@dataclass(frozen=True)
class SessionRecord:
    session_id: str; app_id: str; bank_name: str; bank_country: str
    accounts: tuple[StoredAccount, ...]; valid_until: str; created_at: str
    status: Literal["active", "revoked"] = "active"; bank: BankMeta | None = None
    def account_uid(self, iban: str) -> str | None
    def valid_at(self, now: datetime, margin: timedelta) -> bool      # active and valid_until > now + margin

class SessionStore:
    def __init__(self, path: Path)                     # .psd2demo/session.json (same file as 002)
    def records(self) -> list[SessionRecord]           # [] if missing or unreadable (warning)
    def for_bank(self, app_id, bank_name, bank_country) -> SessionRecord | None   # case-insensitive bank
    def for_account(self, app_id, account: AccountSpec) -> SessionRecord | None   # bank match + iban in accounts
    def upsert(self, record: SessionRecord) -> None    # replaces the same (app_id, bank, country)
    def update(self, record: SessionRecord) -> None    # same key; used for mark-revoked
    def remove(self, predicate: Callable[[SessionRecord], bool]) -> int
```
- File format: `{"schema_version": 2, "sessions": [ {...SessionRecord...} ]}`.
- **Migration:** a v1 object (`schema_version: 1`, single record) maps to one `SessionRecord` with `accounts=[(account_uid, iban_sha256)]` and `bank=None`. It is written back as v2 on the next write only; reads never write. Unknown versions or invalid shapes → warning and treated as empty, and the file is **not** overwritten until a successful consent (then the old file is renamed to `session.json.bak`).
- Writes: read-modify-write in one call, atomic temp file plus `os.replace`, file 0600, dir 0700.

### `eb/models.py` / `eb/client.py` (additions)
```python
Aspsp(+ bic: str | None, beta: bool = False)
Balance(name: str | None, amount: Decimal | None, currency: str | None, balance_type: str | None,
        reference_date: date | None, last_change: datetime | None, last_committed_transaction: str | None)
AccountDetails(raw: dict)                      # fields vary per bank; flattened by output

client.get_balances(uid) -> list[Balance]          # GET /accounts/{uid}/balances, psu=True
client.get_account_details(uid) -> AccountDetails  # GET /accounts/{uid}/details,  psu=True
client.delete_session(session_id) -> None          # DELETE /sessions/{id}; non-GET retry policy (429 only)
client.request_count: int                          # incremented per HTTP attempt (used by 004)
```
- `ApiError` keeps the error body's `code` (V14). `describe()` adds `code=` when it differs from the HTTP status.

### `flow.py` (changed)
```python
class Step(Enum): CONFIG, BANK, CONSENT, ACCOUNT, TRANSACTIONS, BALANCES, DETAILS, SESSIONS, REVOKE, EXPORT

@dataclass
class Context:
    cfg: Config; client: EnableBankingClient; store: SessionStore; reporter: StepReporter
    hooks: ConsentHooks; today: date; now: datetime

@dataclass(frozen=True)
class AccountSession:
    account: AccountSpec; record: SessionRecord; uid: str; reused: bool

class ConsentRequired(DemoError)       # raised only when interactive=False

def bank_for(ctx, account) -> BankMeta + Aspsp     # GET /aspsps match (BANK step); sets client.psu_headers
def ensure_session(ctx, account: AccountSpec, *, interactive: bool = True, fresh: bool = False,
                   consent_days: int | None = None) -> AccountSession
def run_transactions(ctx, a: AccountSession, date_from, date_to) -> TxnResult   # TRANSACTIONS step body
```
`ensure_session` algorithm:
1. **BANK:**
   - `find_bank` (002) → `Aspsp`.
   - `select_psu_headers(available, aspsp.required_psu_headers)` sets `client.psu_headers` (002 rule).
   - Reports `[✓] BANK`.
2. **CONSENT:** `rec = store.for_bank(...)`. Reuse when all hold:
   - not `fresh`, and `rec.valid_at(now, 5 min)`
   - `rec.account_uid(account.iban)` is set
   - `client.get_session(rec.session_id)` returns `AUTHORIZED`, still valid, and that uid is in its accounts

   Then report `reused session valid until …`. If `get_session` fails with 401/404 or any check fails, the record is stale. Then:
   - if `interactive=False`, raise `ConsentRequired(account)`
   - otherwise run a new consent (002 steps: start_auth, notice URL, capture, parse_callback, create_session) with `consent_days` (`None` → the bank maximum, else `min(days, max)`)
   - store `SessionRecord(accounts=all session accounts as (uid, sha256(normalized iban)), bank=BankMeta(...), status=active)` via `upsert`, replacing that bank's record only

   The save follows the 002 V4 rule: if it fails, warn and continue.
3. **ACCOUNT:** `uid = rec.account_uid(account.iban)`. If missing → `AccountNotFound` (masked list of the session's accounts, using only the stored hashes; the masked IBANs come from the `create_session` response when it was a fresh consent).
4. Returns `AccountSession`.

- `run_check(ctx)`: CONFIG, then BANK only if an account resolves; otherwise `GET /aspsps?country=BE` as a credential check.
- TRANSACTIONS errors: an `ApiError` with status 400/422 that isn't `PSU_HEADER_NOT_PROVIDED` gets the hint "the bank may limit history (often 90 days without a new SCA): shorten the range or rerun with --fresh" (R6 AC3).

### `commands.py` (new)
```python
@dataclass
class CommandResult:
    ok: bool; failed_step: Step | None = None; exit_code: int = 0
    summary: dict = field(default_factory=dict)          # JSON-safe after json.dumps(default=str)
    render: Callable[[], str] | None = None               # human output for stdout

COMMANDS: dict[str, Callable[[Context, argparse.Namespace], CommandResult]]
```
Every command wraps its steps in one `try/except (DemoError, ApiError)` that calls `reporter.fail(step, exc)` (the 002 pattern):

| Command | Steps | Summary / render |
|---|---|---|
| `check` | CONFIG, BANK? | bank info |
| `banks [--country] [--filter]` | BANK (list) | `[{name, country, bic, maximum_consent_validity_days, required_psu_headers, beta}]` sorted; `render_banks` |
| `run`/`transactions [--account] [--days\|--from/--to] [--fresh] [--export]` | ensure_session, TRANSACTIONS, EXPORT? | 002 summary plus range; `render_transactions` + `render_summary` |
| `balances [--account] [--fresh] [--export]` | ensure_session, BALANCES, EXPORT? | `{account, balances[]}`; `render_balances` |
| `accounts [--account] [--details] [--fresh]` | ensure_session, DETAILS? | `{accounts[] (selected full IBAN, others masked), details?}`; `render_accounts`, `render_details` |
| `sessions [--offline]` | SESSIONS | `[{bank, country, accounts (count + masked configured), valid_until, local_status, live_status}]`; `render_sessions` |
| `sessions revoke (--account N\|--bank N\|--all) [--yes]` | REVOKE | `[{bank, session_id_short, result}]` |
| `sessions prune` | SESSIONS | `{removed: n}` |

- **`sessions` list:** live status comes from `get_session` per record. An error → `live_status = "unknown (<status>)"`. `--offline` skips the calls.
- **revoke:**
  - Select records: account → its bank's record; bank → case-insensitive name; all → every `active` record.
  - `--all` without `--yes` → `prompt_stderr("Revoke N sessions? [y/N] ")`. Anything but `y`/`yes` → `UsageError("aborted")`.
  - Per record: `delete_session`. Success, 404, or an `ApiError` whose `error` is in `{"SESSION_NOT_FOUND", "ALREADY_CLOSED"}` (when present) → `update(status="revoked")`, `result="revoked"` / `"already closed"`.
  - Other errors leave the record unchanged and fail the command (exit 1) after the remaining records have been tried.
- **prune:** `store.remove(lambda r: r.status == "revoked" or not r.valid_at(now, 0))`.
- **balance labels:** `{"CLBD": "closing booked", "ITAV": "interim available", "ITBD": "interim booked", "XPCD": "expected", "CLAV": "closing available", "OPBD": "opening booked", "FWAV": "forward available"}`. Unknown codes are shown raw.
- **`render_details`:** flattens nested dicts to `a.b` keys and joins lists with `, `, skipping `None`/empty values. IBAN values other than the selected account's are masked.

### `cli.py` (changed)
- Subparsers: `run` (aliases `["transactions"]`), `check`, `banks`, `balances`, `accounts`, `sessions` with a nested subparser `{list (default), revoke, prune}`. Shared parent parsers: `common` (`--config`, `--verbose`, `--json`, app overrides), `account` (`--account`, `--bank`, `--iban`, `--country`), `consent` (`--fresh`, `--capture`, `--consent-days`, `--callback-timeout`), `range` (mutually exclusive `--days` | `--from` + `--to`).
- With no subcommand, `run` is used (002 behaviour kept).
- `main`:
  - parse → `setup_logging` → load config (`ConfigError`/`UsageError` → exit 2) → build `Context` → `COMMANDS[cmd](ctx, args)`.
  - If `--json`, print `{ok, failed_step, steps, summary}` (same envelope keys as 002). Otherwise, if `render`, print `render()`.
  - **The whole body sits inside `try/except KeyboardInterrupt` → 130 (V13).**
- `--export FILE` outside `exports/` → a `reporter.notice` warning: "not git-ignored; contains personal data" (V12).

### `output.py` (additions)
`render_banks`, `render_balances`, `render_accounts`, `render_details`, `render_sessions`. These use a shared `_table(columns, rows, width, flex_col)` helper, which `render_transactions` is refactored to use.

## Data Models (summary)
| Model | Module | Notes |
|---|---|---|
| `AccountSpec` | config | name, bank_name, bank_country, iban |
| `HistorySettings` | config | for 004 |
| `SessionRecord`, `StoredAccount`, `BankMeta` | session_store | schema v2, JSON on disk |
| `Balance`, `AccountDetails`, `Aspsp`(+bic, beta) | eb.models | |
| `Context`, `AccountSession`, `CommandResult` | flow / commands | runtime only |

Session file example:
```json
{"schema_version": 2, "sessions": [{"session_id": "…", "app_id": "…", "bank_name": "KBC", "bank_country": "BE",
  "accounts": [{"uid": "…", "iban_sha256": "…"}], "valid_until": "2027-05-07T10:00:00+00:00",
  "created_at": "…", "status": "active",
  "bank": {"maximum_consent_validity": 15552000, "required_psu_headers": ["Psu-Ip-Address", "Psu-User-Agent"]}}]}
```

## Error Handling
- New: `UsageError` (exit 2), `ConsentRequired` (004; message "consent required for account X", hint `psd2-demo authorize --account X`).
- Config errors are aggregated as in 002, now including account errors (duplicate IBAN, unknown account, half-specified flag account).
- **revoke:** errors are collected per record, and the exit code is 1 if any failed. A partially revoked state is persisted per record.
- **Store:** unreadable → warning, behaves as empty, and is not overwritten silently (a `.bak` is kept on the first write).
- **Ctrl+C:** exit 130 at any point (V13).

## Folded-in 002 Items
- **V12:** export-path warning outside `exports/`.
- **V13:** KeyboardInterrupt covers all of `main`.
- **V14:** `ApiError.code` kept and shown.
- **V16:** record the minor 002 interface deviations in `002/tasks.md`.
- **V17:** tests for the verbose HTTP hook (method, status, truncation, redaction), for `get_session` 401 → stale, and that `GET /sessions/{id}` sends no PSU headers.

## Testing Strategy
- **config:**
  - `[accounts.*]` parsing and order
  - legacy `[demo]`, env and CLI accounts, and the precedence between them
  - the `account()` selection rule and its errors
  - duplicate IBAN detection and `[history]` parsing
- **daterange:** every branch.
- **session_store:**
  - v2 round trip and v1 migration (read without write; written as v2 on upsert)
  - an unreadable file → `.bak` on the first write
  - `for_account`/`for_bank` case-insensitivity, and `remove`
- **client (respx):** balances, details and delete (shapes, PSU headers, no retry on 5xx for DELETE), `request_count`, and the `code` in `describe()`.
- **flow (fakes):**
  - `ensure_session`: reuse; reuse across two accounts of one bank; a stale session triggering a new consent; `interactive=False` → `ConsentRequired`; `consent_days=None` → the bank maximum; save failure → continue
  - the TRANSACTIONS range-error hint
- **commands:** each command's summary and render against golden strings; revoke (success, 404, failure isolation, confirmation prompt, `--yes`); prune.
- **cli:**
  - every subcommand's `--help`
  - the `transactions` alias, and the range flags' mutual exclusion (exit 2)
  - JSON purity for each command
  - Ctrl+C → 130 (monkeypatched command raising KeyboardInterrupt)
- **Regression:** the 002 tests (`run` with legacy config) stay green unchanged, apart from fixture paths.
- **Manual production check:** `banks --filter kbc`, `balances`, `accounts --details`, `run --days 30`, `sessions`, `sessions revoke --account X`, then `run` (new consent).

## Performance Considerations
- `ensure_session` costs `GET /aspsps` plus `GET /sessions/{id}` on reuse, same as 002. `sessions` makes one `GET /sessions/{id}` per record (`--offline` makes none).
- The store is a single small JSON file, read once per command.

## Security Considerations
- Revocation only happens through the explicit `sessions revoke`, and `--all` needs confirmation.
- The store keeps only IBAN hashes. The `accounts`/`sessions` output masks non-selected IBANs. `render_details` masks IBAN-like values except the selected account's.
- The `.bak` file is written 0600 in the 0700 dir and is covered by the `.psd2demo/` ignore.
- The 002 redaction filter is unchanged. Its target IBAN is the selected account's.

## Monitoring and Observability
- The step reporter gains the new steps (`BALANCES`, `DETAILS`, `SESSIONS`, `REVOKE`).
- `--json` gives a uniform envelope `{ok, failed_step, steps, summary}` for every command (002-compatible).
- `--verbose` HTTP logging is unchanged, and the `client.request_count` total is logged at DEBUG at exit.
