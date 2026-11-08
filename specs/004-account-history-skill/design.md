# 004 – Account History Skill: Design

Builds on `003-accounts-and-sessions/design.md`, which must be implemented first. It uses `AccountSpec`, `Config.select()`, `HistorySettings`, `SessionStore` v2 (`SessionRecord.bank` metadata), `ensure_session(interactive=…)`, `resolve_range`, `get_balances`/`get_account_details`, `client.request_count`, and `CommandResult`.

## Overview
- **`authorize`** (interactive): one consent per bank, for all selected accounts, with the bank's maximum validity. Uses `ensure_session(interactive=True)`.
- **`history`** (non-interactive): for each selected account it uses the cache when fresh, else verifies the stored session read-only, then fetches transactions (plus balances/details) and builds the per-account entry. All of it is assembled into one versioned JSON document.
- **Core** `history.py`:
  - `fetch_history()` builds the document. The CLI command and the Python API are thin wrappers, and the Claude Code skill calls the CLI.
  - `contract.py` owns serialization and the schema version.
  - `schemas/account-history.v1.json` is the published contract.

## Architecture

```
 Claude Code skill (.claude/skills/bank-account-history/SKILL.md)
        │ runs
        ▼
 psd2-demo history ──► commands.history ──► history.fetch_history(...) ◄── Python applications
 psd2-demo authorize ─► commands.authorize ─► flow.ensure_session(interactive=True) per bank
                                               │
 history.fetch_history:                         │
   load_config ─► client ─► for account in cfg.select(names):
        cache.get(key, max_age) ──hit──► entry(cached=True)
        │ miss
        ▼
        resolve_session(read-only): store.for_account → local validity → client.get_session
        │      └─ invalid ─► entry(status=consent_required)
        ▼
        psu headers from SessionRecord.bank (fallback GET /aspsps once per bank)
        iter_transactions ─► normalize ─► contract.txn ─► [balances] [details] ─► entry(ok)
        │      └─ ApiRateLimited ─► stale cache? entry(cached, stale) : entry(rate_limited)
        │      └─ other errors   ─► entry(error{step,…})
        ▼
        cache.put(key, entry)
   contract.document(range, include, entries) ─► dict  (validated by schema in tests)
```

## Components and Interfaces

### `history.py` (new)
```python
INCLUDE_PARTS = frozenset({"balances", "details", "raw"})

@dataclass(frozen=True)
class HistoryRequest:
    accounts: tuple[str, ...] | None         # None → all configured
    date_from: date; date_to: date
    include: frozenset[str]
    max_age: int | None                      # minutes; None → no cache reads
    use_cache: bool = True

def fetch_history(config_path=None, accounts=None, days=None, date_from=None, date_to=None, include=None,
                  *, max_age=None, use_cache=True, env=None, client=None, today=None, now=None) -> dict
def build_document(cfg: Config, client, store: SessionStore, cache: HistoryCache,
                   req: HistoryRequest, *, now: datetime) -> dict
```
- **`fetch_history`:**
  - `env` defaults to `os.environ`. Loads config, which raises `ConfigError`.
  - Resolves the range with `resolve_range(today, days, date_from, date_to, cfg.history.days)` and turns `UsageError` into `ConfigError` (R5 AC2: only `ConfigError` escapes).
  - Resolves `include`: an argument replaces `cfg.history.include`. Unknown parts → `ConfigError`.
  - `max_age` defaults to `cfg.history.max_age`.
  - Builds `JwtSigner` and `EnableBankingClient` unless `client` is injected.
  - Returns `build_document(...)`.
  - Prints nothing. Logging goes to the `psd2demo` logger only; the package installs a `NullHandler` in `psd2demo/__init__.py`.
- **`build_document`:** processes accounts sequentially in config order. Each account runs inside a `try/except Exception`, so per-account isolation also covers unexpected errors (`status=error`, `step="internal"`). `KeyboardInterrupt` propagates.
- **The store is read-only here:** `history` never calls `upsert`/`update`/`remove`.

#### Per-account algorithm
1. `key = cache.key(app_id, iban_sha256, date_from, date_to, include)`. When `use_cache` and `max_age` is set: `hit = cache.get(key, max_age, now)` → return `hit` with `cached=True` and `api_calls=0`.
2. `calls0 = client.request_count`.
3. **SESSION** (read-only):
   - `rec = store.for_account(app_id, account)`. Missing, `rec.valid_at(now, 5 min)` false, or `rec.account_uid(iban)` missing → `consent_required`.
   - `client.get_session(rec.session_id)`: `ApiAuthError`/`ApiNotFound`, a status other than `AUTHORIZED`, or the uid not in its accounts → `consent_required`.
4. **PSU:**
   - `required = rec.bank.required_psu_headers` when `rec.bank` is set. Otherwise call `client.get_aspsps(country)` once per bank per run (memoized in a dict) and match by name. No match → `required = ()`.
   - Then `client.psu_headers = select_psu_headers(base_psu_headers, required)[0]`. `base_psu_headers` is built once per run from config (002 `make_client` logic).
5. **TRANSACTIONS:** `pages = list(client.iter_transactions(uid, from, to))` → `normalize` → `sort_txns` → `contract.transactions(account, txns, raws if "raw" in include)`. `truncated = pages[-1].truncated`, which adds a warning when set.
6. **BALANCES / DETAILS** (if included): each runs in its own try. On `ApiRateLimited` the whole account is treated as rate-limited (go to step 8). On any other error the part becomes `null` and a warning is added: `{"part": "balances", "message": …, "http_status": …}`.
7. `entry = contract.account_entry(status="ok", …, api_calls=client.request_count - calls0)`, then `cache.put(key, entry, now)` when `use_cache` (always written on success, even when `max_age` is None, so later runs can use it).
8. **Errors:**
   - `ApiRateLimited` at any step → `stale = cache.get(key, None, now)` (any age). If found, return it with `cached`, `stale`, the original `fetched_at`, a `rate_limited` warning, and `status` from the cached entry (`ok`). Otherwise `status=rate_limited` with an error hint.
   - Other `ApiError`/`DemoError` → `status=error`, with `error={step, message, http_status, api_error, hint}`.
   - Nothing is cached on failure.

### `contract.py` (new)
```python
SCHEMA_VERSION = "1"
def transaction(account: AccountSpec, txn: Txn, raw: dict | None, ordinal: int) -> dict
def transactions(account: AccountSpec, txns: list[Txn], raws: list[dict] | None) -> list[dict]   # assigns ids
def balance(b: Balance) -> dict
def account_entry(*, account, status, error=None, warnings=(), session_valid_until=None, fetched_at=None,
                  truncated=False, cached=False, stale=False, api_calls=0, currency=None,
                  transactions=(), balances=None, details=None, include=frozenset()) -> dict
def document(*, generated_at: datetime, tool_version: str, date_from, date_to, include, entries) -> dict
def stable_id(account_iban: str, txn: Txn, ordinal: int) -> str
```
- **Key order is fixed** by building dicts in schema order. Values:
  - `Decimal` → `format(d, "f")` with an explicit `+`/`-` sign
  - `date` → `isoformat()`
  - `datetime` → UTC `isoformat()` ending in `Z`
  - missing → `None`
- **`stable_id`:**
  - Use `txn.reference` (entry_reference, else transaction_id) when present: `"ref:" + reference`.
  - Otherwise `"h:" + sha256("|".join([iban, effective_date, amount, currency, counterparty_name or "", counterparty_iban or "", remittance or "", str(ordinal)]))[:32]`.
  - `ordinal` is the 0-based index among transactions with an identical hash tuple, in API order, so two identical €3.50 coffees on the same day get different ids. They stay stable as long as the bank's order is stable, which is documented as a limitation.
- **Transactions keep only transaction fields:** the `transactions()` order is newest first (002 `sort_txns`). `transactions()` computes ordinals before sorting (API order), and the id is attached afterwards.
- **`currency`:** the session account's currency, taken from the most common currency among the transactions/balances; `null` if unknown.
- **`summary`:** `{"accounts": {"ok": n, "consent_required": n, "rate_limited": n, "error": n}, "totals": {CUR: {count, credits, debits, net}}}`, computed over `ok` entries only, with the 002 `summarize`.

### `cache.py` (new)
```python
class HistoryCache:
    def __init__(self, directory: Path)        # .psd2demo/cache/, 0700
    @staticmethod
    def key(app_id, iban_sha256, date_from, date_to, include) -> str      # sha256 hex
    def get(self, key, max_age_min: int | None, now) -> dict | None       # None max_age → any age
    def put(self, key, entry: dict, now) -> None                           # atomic, 0600
    def prune(self, older_than: timedelta, now) -> int                     # called by `sessions prune`
```
- File `<key>.json`: `{"cache_version": 1, "stored_at": iso, "entry": {...account entry...}}`. Unreadable files are treated as a miss, with a warning.
- The cache holds full IBANs and transactions, so it gets the same protections as exports: 0600, under the git-ignored `.psd2demo/`. `sessions prune` also prunes cache files older than 7 days.

### `commands.py` (additions)
```python
def authorize(ctx, args) -> CommandResult
def history(ctx_or_none, args) -> CommandResult
```
- **`authorize [--account N ... | --all] [--fresh] [--consent-days N] [--status]`:**
  - The targets are the selected accounts, grouped by `(bank_name, bank_country)` in config order.
  - Per bank: if a record covers all of that bank's target accounts with more than 7 days left and isn't `--fresh`, skip it ("valid until …"). Otherwise `ensure_session(ctx, first_account, interactive=True, fresh=True, consent_days=args.consent_days)`, i.e. the bank maximum by default.
  - After each consent, report every target account at that bank as `authorized`, or `missing` (IBAN not in the session; hint: select it at the bank or link it in the control panel).
  - **`--status`:** no consent. Per account: `authorized`, `expiring` (< 7 days), or `consent_required`, using the record plus `get_session`.
  - Exit 0 when every target account is authorized, else 1.
- **`history`:**
  - Builds args → `fetch_history(config_path=args.config, accounts=args.account or None, days=…, include=parse_include(args.include), max_age=0 if args.no_cache else args.max_age, use_cache=not args.no_cache)`.
  - `parse_include`: comma-separated, `"none"` → empty set.
  - Exit code: all `ok` → 0; none `ok` → 1; mixed → 3 (`EXIT_PARTIAL`). An empty account selection is a `ConfigError` → 2.
  - Output:
    - The document goes to stdout as `json.dumps(doc, indent=2, ensure_ascii=False)`, or to `--output FILE` (atomic, 0600; parent dir created; the V12 notice when outside `exports/`).
    - In `--output` mode nothing goes to stdout, so stdout stays empty and only the file is written.
    - `history` has no human renderer. `--json` is implicit and accepted as a no-op flag.
  - Reporter: quiet unless `--verbose`, then per-account progress goes to stderr.

### `cli.py` (additions)
- `authorize` subparser: `account` parent (with `--account` repeatable, `--all`), `consent` parent, `--status`.
- `history` subparser:
  - `--account` (repeatable), the `range` parent, `--include`, `--output`, `--max-age MIN`, `--no-cache`, `--verbose`, `--config`
  - no `--bank`/`--iban`: history runs over configured accounts only, which keeps the cache and session keys well defined
- `ConfigError`/`UsageError` from `history` → stderr message, exit 2, and no document.

### JSON Schema `schemas/account-history.v1.json`
- Draft 2020-12 with `additionalProperties: false` at every level, so accidental contract drift fails the tests.
- **Top level** (all fields required):
  - `schema_version` (`const "1"`), `generated_at` (date-time), `tool {name, version}`
  - `range {date_from, date_to}` (date), `include` (array, enum `balances`/`details`/`raw`)
  - `accounts[]`, `summary`
- **`accounts[]`:** required `name, bank, country, iban, currency, status (enum), error, warnings, session_valid_until, fetched_at, truncated, cached, stale, api_calls, transactions`. Optional keys `balances`, `details`, present when included.
  - `error`: null or `{step, message, http_status, api_error, hint}`
- **transaction:** required `id, booking_date, value_date, transaction_date, amount (string, pattern ^[+-]\d+(\.\d+)?$), currency, counterparty_name, counterparty_iban, remittance, status, reference, out_of_range`. Optional `raw` (object, only with include `raw`).
- **balance:** `name, type, type_label, amount, currency, reference_date, last_change`.
- `details`: object with free-form properties (bank-specific).
- Example document: `schemas/account-history.v1.example.json`, generated from the test fixtures by a test helper and kept in sync by a test.

### Claude Code skill `.claude/skills/bank-account-history/SKILL.md`
Frontmatter:
```yaml
---
name: bank-account-history
description: Fetch transaction history, balances and account details for the user's configured Belgian bank accounts (PSD2 via Enable Banking) as JSON, then answer questions about spending, income or balances over a period.
when_to_use: User asks about their bank transactions, payments, spending, income, balances or account activity for a period, e.g. "what did I spend last month", "show my KBC transactions this week", "balances of my accounts".
argument-hint: "[days] [account]"
allowed-tools: Bash(.venv/bin/psd2-demo history *) Read
---
```
Body sections (concise, imperative):
1. **Run:** `.venv/bin/psd2-demo history --days N [--account NAME] [--include balances,details] --output exports/history-<date>.json` from `${CLAUDE_PROJECT_DIR}`. Choose N from the request (default from config). Add `--include balances` only for balance questions.
2. **Read** the output file with Read, never `cat` it into the conversation.
3. **Interpret** each `accounts[].status`:
   - `ok`: use the data.
   - `consent_required`: tell the user to run `! .venv/bin/psd2-demo authorize --account NAME` themselves. It's interactive; never run it.
   - `rate_limited`: say so; if `stale`, say the data is from `fetched_at`.
   - `error`: report `error.message` and `error.hint`.

   Exit code 3 means partial results.
4. **Answer** with aggregates and only the relevant transactions. Don't print full IBANs or the whole list unless asked. Amounts are strings, so parse them as decimals.
5. **Limits:**
   - one fetch per question, and rely on the cache (don't add `--no-cache`)
   - never send the data to external services or tools, or write it outside `exports/`
   - never revoke or change sessions
6. **Example:** "How much did I spend at Delhaize in the last 30 days?" → `history --days 30 --output exports/history-2026-11-08.json`, then sum the negative `amount` values where `counterparty_name` contains "Delhaize".

## Data Models
| Model | Module | Notes |
|---|---|---|
| `HistoryRequest` | history | resolved inputs |
| account entry / document | contract | plain dicts in schema order (no dataclasses; JSON-first) |
| cache file | cache | `{cache_version, stored_at, entry}` |
| `HistorySettings` | config (003) | days, include, max_age |

Config additions (`config.example.toml`):
```toml
default_account = "main"

[accounts.main]
bank_name = "KBC"
iban = "BE68 5390 0754 7034"

[accounts.savings]
bank_name = "Belfius"
iban = "BE71 0961 2345 6769"

[history]
days = 30
include = []          # any of "balances", "details", "raw"
max_age = 60          # minutes a cached account result stays fresh
```

## Error Handling
| Condition | Result |
|---|---|
| Config/usage error | `ConfigError` (Python API) / exit 2 + stderr (CLI), no document |
| No valid session for an account | `status=consent_required`, `error.step="SESSION"`, hint `psd2-demo authorize --account X` |
| 429 at any step | stale cache → `ok` + `cached/stale` + warning; else `status=rate_limited` |
| Balances/details failure (non-429) | part `null` + warning; account stays `ok` |
| Other API/network error | `status=error`, `error{step: SESSION/TRANSACTIONS/…, http_status, api_error, hint}` |
| Unexpected exception | `status=error`, `step="internal"`, message `type: str(exc)` (redacted) |
| Truncated pagination | `truncated=true` + warning; status `ok` |
| Output file write fails | stderr error, exit 1, document not printed to stdout (no silent fallback) |
| Ctrl+C | exit 130, no partial file (atomic write) |

## Testing Strategy
- **`contract`:**
  - value formatting (sign, decimals, dates, null keeps the key) and fixed key order
  - `stable_id`: reference vs hash, ordinals for identical transactions, determinism across runs, and independence from sorting
- **`build_document`** with a fake client and a tmp store/cache:
  - every status; partial runs; per-account isolation (an exception in account 1, account 2 still `ok`)
  - cache hit, miss, and expiry; `--no-cache`; 429 with a stale fallback and without
  - balances/details failure → warning; `include` handling (`raw` present only when requested)
  - `api_calls` counting
  - PSU from `SessionRecord.bank`, and the `/aspsps` fallback memoized per bank
  - the store is not modified (file mtime and content unchanged)
- **Schema:** the `jsonschema` dev dependency validates documents from every test scenario against `schemas/account-history.v1.json`. The example file regenerates identically.
- **`fetch_history` (Python API):** `capsys` shows nothing on stdout; it raises only `ConfigError` for a bad config, unknown include or bad range.
- **CLI:**
  - `history` exit codes 0/1/2/3/130
  - stdout is valid JSON and nothing else; `--output` leaves stdout empty, writes the file 0600, and the content parses
  - `authorize`: skip-when-valid, `--fresh`, `--status`, missing-account reporting, and the bank-maximum default for consent length
- **Skill:**
  - a test asserts `SKILL.md` frontmatter parses as YAML with `name`/`description`/`allowed-tools`
  - every command line in the body uses only flags that `history --help` accepts (parsed from the file)
- **Manual production check:** `authorize --all` → `history --days 30 --include balances,details` → rerun (cache hit, `api_calls: 0`) → ask Claude Code "what did I spend this week?" and confirm the skill is used.

## Performance Considerations
- Sequential per account. Calls per account on a miss:
  - `GET /sessions/{id}` (Enable Banking, not the bank)
  - transaction pages
  - +1 each for balances/details
  - the `/aspsps` fallback only for migrated v1 records
- A fully cached run makes zero network calls: no signer use and no client requests. The client is created lazily on first need.
- Cache reads are one small file per account.

## Security Considerations
- The document, cache and `--output` files contain full own-account IBANs and transaction data. All are 0600, and the cache sits under the git-ignored `.psd2demo/`. A notice is shown for output outside `exports/`.
- `history` and the skill never start a consent, write sessions, or revoke. The skill's `allowed-tools` limits pre-approval to `psd2-demo history` plus `Read`, and `authorize` isn't pre-approved.
- Redaction (002) still applies to logs. Unexpected-exception messages go through `RedactingFilter.redact` before being put into the document.
- The Python API doesn't log secrets and doesn't install handlers (`NullHandler`).

## Monitoring and Observability
- Each account entry carries `api_calls`, `cached`, `stale`, `fetched_at`, `warnings` and `truncated`. Top-level `summary.accounts` counts statuses, so applications can alert on `consent_required` or `rate_limited`.
- `--verbose` prints per-account progress and HTTP debug lines (redacted) to stderr.
- `authorize --status` is the health check for unattended use (consents about to expire). The docs' cron example runs it weekly.
