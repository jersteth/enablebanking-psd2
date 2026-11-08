# 002 – Production Transactions Demo: Design

## Overview
A Python CLI (`psd2-demo`) with two commands:
- `run` (the default): config → bank check → consent (or reuse a stored session) → account match → transactions for the last 10 days → table, summary, and pass/fail exit code.
- `check`: config + key + JWT + `GET /aspsps`, with no consent.

An `eb` subpackage holds a thin, synchronous Enable Banking client with no CLI/IO concerns, so 001 can reuse it. The setup guide lives in `documentation/`.

### Technology
| Concern | Choice | Reason |
|---|---|---|
| Language | Python ≥ 3.11 (dev box: 3.14) | `tomllib` in the standard library; Enable Banking's own samples are Python |
| HTTP | `httpx` | timeouts, clean API, mockable with `respx` |
| JWT | `PyJWT[crypto]` (pulls in `cryptography`) | RS256 signing from a PEM key |
| CLI | `argparse` (stdlib) | no extra dependency for two commands |
| Config | TOML via `tomllib` + env vars | stdlib |
| Tests | `pytest`, `respx` | offline HTTP mocking |
| Packaging | `pyproject.toml` (hatchling), `src/` layout, venv + pip | `uv` not installed |

## Architecture

```
            ┌──────────────────────── psd2demo ────────────────────────┐
 user ──►   │ cli.py ──► flow.py (Step orchestration, StepReporter)    │
 (terminal) │              │                                           │
            │   ┌──────────┼───────────┬──────────────┬──────────────┐ │
            │   ▼          ▼           ▼              ▼              ▼ │
            │ config.py  consent.py  session_store.py transactions.py output.py
            │ iban.py    (state, browser,  (JSON file,   (normalize,   (tables,
            │            paste/listener)   0600, atomic) sort, summary) JSON export)
            │              │                                           │
            │              ▼                                           │
            │   eb/ ── client.py ──► auth.py (JwtSigner)               │
            │          models.py, errors.py                            │
            └──────────────┼───────────────────────────────────────────┘
                           ▼ HTTPS, Bearer JWT
                  api.enablebanking.com ──► Belgian bank (redirect, in browser)
```

Run sequence:
```
cli → load_config → validate IBAN → JwtSigner(key)
flow: [CONFIG] ok
      [BANK]   client.get_aspsps(BE, personal) → match name → max_consent_validity
      [CONSENT] store.load() → matches(bank, iban)? → client.get_session(id)
                   AUTHORIZED & not expired & uid in accounts → reuse
                 else: state=secrets.token_urlsafe(32)
                       client.start_auth(...) → url → webbrowser.open + print
                       capture(paste|listener, timeout) → parse_callback(state)
                       client.create_session(code) → store.save()
      [ACCOUNT] match IBAN in session accounts → uid
      [TRANSACTIONS] client.iter_transactions(uid, from, to) → normalize → sort → summary
      output.render(); optional export → exit 0
```

## Repository Layout
```
pyproject.toml            # deps, entry point psd2-demo = psd2demo.cli:main
config.example.toml       # committed template
.gitignore                # config.toml, *.pem, .psd2demo/, exports/, .venv/
README.md                 # quick start + link to setup guide
documentation/index.md
documentation/enablebanking-setup.md
src/psd2demo/
  __init__.py  __main__.py  cli.py  config.py  iban.py
  consent.py  session_store.py  transactions.py  flow.py  output.py  log.py
  eb/__init__.py  eb/auth.py  eb/client.py  eb/models.py  eb/errors.py
tests/
  fixtures/{aspsps,auth,session_create,session_get,transactions_p1,transactions_p2,error_*}.json
  test_*.py
```

## Components and Interfaces

### `eb/auth.py`
```python
class JwtSigner:
    def __init__(self, app_id: str, private_key_pem: bytes, ttl: int = 3600, clock=time.time): ...
    def token(self) -> str   # cached; re-signed when < 60 s remain
```
- Header `{"typ":"JWT","alg":"RS256","kid":app_id}`. Claims `iss="enablebanking.com"`, `aud="api.enablebanking.com"`, `iat=now`, `exp=now+ttl`.
- `ttl` must be in 1..86400, otherwise `ValueError`.
- The key is loaded once with `cryptography`'s `load_pem_private_key` so a bad key fails at startup.

### `eb/client.py`
```python
class EnableBankingClient:
    BASE_URL = "https://api.enablebanking.com"
    def __init__(self, signer: JwtSigner, *, base_url=BASE_URL, timeout=30.0,
                 psu_headers: dict[str, str] | None = None, transport=None): ...
    def get_aspsps(self, country: str, psu_type: str = "personal") -> list[Aspsp]
    def start_auth(self, aspsp: Aspsp, *, valid_until: datetime, state: str,
                   redirect_url: str, psu_type: str = "personal") -> AuthStart
    def create_session(self, code: str) -> Session
    def get_session(self, session_id: str) -> SessionStatus
    def get_transactions_page(self, account_uid: str, date_from: date, date_to: date,
                              continuation_key: str | None) -> TransactionsPage
    def iter_transactions(self, account_uid, date_from, date_to, *, max_pages=50
                          ) -> Iterator[TransactionsPage]
```
- `_request(method, path, *, params=None, json=None, retry_post=False)` adds `Authorization: Bearer`, `Accept: application/json`, and the PSU headers (only on AIS data calls and `/auth`).
- Retries: GET retries on 429, 5xx, and `httpx.TransportError`. POST retries **only on 429**, because the `/sessions` `code` is single-use. At most 3 retries. Backoff waits for `Retry-After` (seconds, capped at 30) or else 1, 2, 4 s. `sleep` is injectable.
- `iter_transactions` stops when `continuation_key` is missing or empty, repeats an earlier key, or `max_pages` is reached (it then logs a warning and sets `truncated=True` on the last page).
- `start_auth` body:
  `{"access":{"valid_until": iso8601Z}, "aspsp":{"name","country"}, "state", "redirect_url", "psu_type"}`.
- Any non-2xx response is turned into an `ApiError` subclass (see Error Handling). The response body is kept, but no request headers.

### `config.py`
```python
@dataclass(frozen=True)
class Config:
    app_id: str; key_path: Path; redirect_url: str
    bank_name: str; bank_country: str = "BE"; iban: str
    capture_mode: Literal["paste","listener","auto"] = "auto"
    consent_days: int = 1; callback_timeout: int = 300
    psu_ip_address: str | None = None
    state_dir: Path = Path(".psd2demo")
def load_config(path: Path | None, env: Mapping[str,str], overrides: dict) -> Config
```
- Precedence: CLI flags > env > TOML file > defaults. The file is `--config`, else `./config.toml` if it exists.
- TOML keys and env vars:

| TOML (`[enablebanking]` / `[demo]`) | Env | Flag |
|---|---|---|
| `app_id` | `EB_APP_ID` | `--app-id` |
| `key_path` | `EB_KEY_PATH` | `--key` |
| `redirect_url` | `EB_REDIRECT_URL` | `--redirect-url` |
| `bank_name` | `EB_BANK_NAME` | `--bank` |
| `bank_country` | `EB_BANK_COUNTRY` | `--country` |
| `iban` | `EB_IBAN` | `--iban` |
| `capture_mode` | `EB_CAPTURE_MODE` | `--capture` |
| `consent_days` | `EB_CONSENT_DAYS` | `--consent-days` |
| `psu_ip_address` | `EB_PSU_IP` | `--psu-ip` |

- Validation collects **all** problems into one `ConfigError`: required fields missing; key file missing or unreadable; key file mode looser than 0600 (warning only); `app_id` not a UUID (warning only); `redirect_url` not absolute http(s); IBAN invalid.
- `capture_mode="auto"` resolves to `listener` if `redirect_url` is `http://localhost|127.0.0.1:<port>`, otherwise `paste`. Choosing `listener` with a non-loopback URL is a `ConfigError`.

### `iban.py`
`normalize(s) -> str` (strip spaces, uppercase) · `validate(s) -> None` (regex `^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$`, mod-97 == 1, Belgian length 16 when the prefix is `BE`) · `mask(s) -> str` (`BE68 **** **** 0414`, keeps the country, check digits, and last 4).

### `consent.py`
```python
def new_state() -> str                     # secrets.token_urlsafe(32)
def compute_valid_until(now: datetime, consent_days: int, max_validity_s: int | None) -> datetime
def open_browser(url: str) -> None         # webbrowser.open; always also printed by caller
def capture_paste(prompt_fn, timeout: int) -> str        # input() in daemon thread, join(timeout)
def capture_listener(redirect_url: str, timeout: int) -> str  # one-shot server
def parse_callback(url: str, expected_state: str) -> str     # returns code
```
- `compute_valid_until` = `now + min(consent_days*86400, max_validity_s or ∞)`, minus 60 s of safety margin, in UTC.
- Listener: `http.server.HTTPServer(("127.0.0.1", port))`. It serves one GET on the redirect path, answers with a small HTML page ("You can close this tab"), then shuts down. `server.timeout` plus a deadline loop gives the timeout. The path must match `redirect_url`'s path.
- `parse_callback`: query parsed with `urllib.parse`. `error` present → `ConsentDenied(error, error_description)`. `state` missing or mismatched (compared with `hmac.compare_digest`) → `StateMismatch`. `code` missing → `ConsentError`.

### `session_store.py`
```python
class SessionStore:
    def __init__(self, path: Path): ...      # default .psd2demo/session.json
    def load(self) -> StoredSession | None   # None if missing or corrupt (logs a warning)
    def save(self, s: StoredSession) -> None # mkdir 0700; temp file 0600 in same dir; os.replace
    def clear(self) -> None
```
- It holds a single record. A stored session is reused only if `app_id`, `bank_name`, `bank_country`, and `iban_sha256` all match.

### `transactions.py`
```python
def normalize(raw: dict, date_from: date, date_to: date) -> Txn
def sort_txns(txns) -> list[Txn]           # booking_date or value_date or transaction_date, newest first
def summarize(txns) -> dict[str, Summary]  # per currency
```
- Sign: `DBIT` → negative, `CRDT` → positive. Amounts are parsed with `Decimal`.
- Counterparty: `DBIT` → `creditor` / `creditor_account.iban`; `CRDT` → `debtor` / `debtor_account.iban`.
- Remittance: `" / ".join(remittance_information or [])`.
- Reference: `entry_reference or transaction_id or None`.
- `out_of_range`: true when the effective date falls outside `[date_from, date_to]`.
- Unknown or missing fields are tolerated: a `None` field is shown as `-`.

### `flow.py`
```python
class Step(Enum): CONFIG, BANK, CONSENT, ACCOUNT, TRANSACTIONS
class StepReporter:  start(step), ok(step, detail), fail(step, err)  # prints "[✓] BANK  KBC Bank (BE)"
def run_demo(cfg, client, store, reporter, *, fresh: bool, today: date, export: Path | None) -> RunResult
def run_check(cfg, client, reporter) -> RunResult
```
- `run_demo` order: BANK → CONSENT (reuse unless `fresh`) → ACCOUNT → TRANSACTIONS.
- Each step catches `DemoError` / `ApiError`, reports it with its hint, and returns a failure.
- Bank match: case-insensitive exact match on `name`. If there is none, the error lists up to 10 substring matches, or else up to 10 names in total.
- Reuse check: `get_session` must have status `AUTHORIZED`, `access.valid_until > now + 5 min`, and the stored `account_uid` in `accounts`. Anything else → `store.clear()` and a new consent. If `get_session` returns 404 or 401, it is also treated as invalid, so the flow continues rather than failing.
- Account match: compare `normalize(account_id.iban)`. If there is no match → `AccountNotFound`, listing masked IBANs, names, and currencies.
- `today` is injectable, and dates use the local date (`date.today()`).

### `output.py`
- `render_transactions(txns, summary, account)`: fixed columns `Booked | Value | Amount | Cur | Counterparty | Remittance | Status | Ref`. Remittance is truncated to the terminal width (`shutil.get_terminal_size`). Out-of-range rows are marked `*`, with a legend.
- `render_summary`: count, credits, debits, and net per currency. "No transactions in range" when the list is empty.
- `export_json(path, run_meta, txns, raw_pages)`: `{meta:{bank, iban_masked, date_from, date_to, generated_at}, transactions:[...normalized], raw_pages:[...]}`. Written with mode 0600.
- `--json` on `check` and `run` sends the machine-readable result to stdout instead of the tables. That is separate from `--export <file>` (R6 AC6). The design names this flag `--export` to avoid ambiguity.

### `cli.py`
```
psd2-demo [run] [--config F] [--fresh] [--export FILE] [--verbose] [config overrides…]
psd2-demo check [--config F] [--verbose] [config overrides…]
```
- With no subcommand, `run` is used. `main()` returns the exit code, and `__main__.py` calls `sys.exit(main())`.
- Exit codes: `0` success, `1` step failure (API, consent, account), `2` config/usage error, `130` Ctrl+C.

### `log.py`
- Uses `logging` and the `psd2demo` logger. `--verbose` → DEBUG with method, URL, status, elapsed time, and the response body truncated to 2 KB.
- A `RedactingFilter` masks `Bearer <...>`, `code=<...>`, `state=<...>`, `"code": "..."`, and IBANs other than the target (regex), applied to every record.

## Data Models (`eb/models.py`, frozen dataclasses with `from_json`)
```python
Aspsp(name: str, country: str, maximum_consent_validity: int | None, psu_types: list[str])
AuthStart(url: str, authorization_id: str)
Account(uid: str, iban: str | None, name: str | None, currency: str | None, raw: dict)
Session(session_id: str, accounts: list[Account], valid_until: datetime | None, raw: dict)
SessionStatus(status: str, account_uids: list[str], valid_until: datetime | None)
       # status ∈ AUTHORIZED, PENDING_AUTHORIZATION, EXPIRED, INVALID, CANCELLED, REVOKED, CLOSED
TransactionsPage(transactions: list[dict], continuation_key: str | None, raw: dict, truncated=False)
```
App-level models:
```python
StoredSession(session_id, app_id, bank_name, bank_country, iban_sha256, account_uid,
              valid_until: str, created_at: str)          # JSON file, schema_version: 1
Txn(booking_date, value_date, amount: Decimal, currency, counterparty_name, counterparty_iban,
    remittance, status, reference, out_of_range: bool)
Summary(count, credits: Decimal, debits: Decimal, net: Decimal)
RunResult(ok: bool, failed_step: Step | None, exit_code: int)
```
- `Account.iban` comes from `account_id.iban`. `valid_until` comes from `access.valid_until`. Datetimes are parsed with `datetime.fromisoformat` (which accepts `Z` on 3.11+).
- Parsers ignore unknown fields. A missing required field (`session_id`, `url`, `uid`) raises `ApiSchemaError`.

## Error Handling
```
DemoError(message, hint=None)                      # base, caught by flow
├─ ConfigError(problems: list[str])                → exit 2
├─ ConsentError → ConsentDenied(error, description), StateMismatch, CallbackTimeout
├─ BankNotFound(candidates), AccountNotFound(accounts)
└─ ApiError(status, method, endpoint, code, message, detail, error)
   ├─ ApiAuthError (401/403)   hint: check app_id/kid, key matches app, system clock, app Active
   ├─ ApiRateLimited (429)     hint: bank/aggregator limit; PSD2 unattended limit 4/day
   ├─ ApiNotFound (404)
   ├─ ApiServerError (5xx)
   └─ ApiSchemaError           unexpected response shape
TransportError wrapper → ApiError(status=0) after retries are exhausted
```
- Errors print as `[✗] STEP  <message>` plus `      status=… endpoint=… error=… detail=…` and `      hint: …`.
- Hints for known `error` values: `ACCESS_DENIED` (consent denied or account not linked) and `ASPSP_ERROR` (bank-side issue; retry later).
- Ctrl+C at any point exits 130. No session is saved unless `create_session` succeeded.

## Testing Strategy
- **Unit (offline, pytest + respx)**:
  - `JwtSigner`: decode with the public key and assert header and claims; check the TTL bounds and that caching re-signs near expiry (fake clock).
  - `EnableBankingClient`: request shape for each endpoint (path, params, body, headers); error mapping per status; retry policy (GET 503→200, POST 503 not retried, 429 + `Retry-After`); pagination (two pages, repeated key, `max_pages`).
  - `config`: precedence, aggregated errors, capture mode resolution.
  - `iban`: valid and invalid samples, masking.
  - `consent`: `parse_callback` (ok, error, state mismatch, missing code), `compute_valid_until` clamping, listener (real socket on an ephemeral port, request from a thread, timeout).
  - `session_store`: round trip, 0600 mode, corrupt file → None, atomic write (no temp file left behind).
  - `transactions`: sign, counterparty side, out-of-range flag, sort fallback, multi-currency summary.
  - `flow`: with a fake client: reuse path, fresh path, bank not found, account not found, zero transactions = pass; check exit codes.
- **Fixtures**: JSON shaped like the Enable Banking reference schemas, with synthetic IBANs (valid checksums).
- **Manual E2E (production)**: run `psd2-demo check` and then `psd2-demo run`, following `documentation/enablebanking-setup.md`. Never automated.
- Command: `pytest` (configured in `pyproject.toml`). A single test: `pytest tests/test_client.py::test_name`.

## Performance Considerations
- A single `httpx.Client` with connection reuse. Expected traffic per run: 1 `aspsps` + 1–3 auth/session calls + N transaction pages.
- The JWT is cached for the process lifetime (TTL of 1 h by default).
- `max_pages=50` bounds the work if a bank keeps returning continuation keys.

## Security Considerations
- The private key is read from `key_path` only, kept in memory, and never written or logged. The guide places it outside the repo with `chmod 600`.
- `state`: 256-bit `secrets` token, compared in constant time. A callback whose `state` doesn't match is rejected.
- The listener binds `127.0.0.1` only, serves one request, has a timeout, and never echoes query parameters into its HTML.
- The session file and exports are 0600, and `.psd2demo/` is 0700. All of these are git-ignored along with `config.toml` and `*.pem`.
- Only the IBAN's SHA-256 is stored in the session file. Output masks IBANs of accounts other than the target.
- `RedactingFilter` is applied to all log records. No raw request headers are logged.
- TLS verification uses the httpx defaults and is never disabled.

## Monitoring and Observability
- The step reporter gives a human-readable trace of each run (step, status, short detail such as `reused session valid until …` or `14 transactions, 2 pages`).
- `--verbose` turns on DEBUG HTTP logs (redacted), including each request's elapsed time and the Enable Banking `error` code.
- The exit code shows pass or fail to scripts. `--json` gives a structured result `{ok, failed_step, steps:[{name, ok, detail}], summary}`.
- `--export` keeps raw API pages for comparing with the Enable Banking docs or reporting issues to their support.
