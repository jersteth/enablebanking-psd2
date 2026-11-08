# 002 – Production Transactions Demo: Tasks

Refs: `requirements.md` (R1–R8), `design.md`. Mark tasks `[x]` when done and their tests pass.

## Phase 1 – Project Skeleton and Pure Logic ✅
**Goal**: an installable package with offline, fully tested domain logic (config, IBAN, transaction normalization).
**Verify (user)**: `python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]' && pytest` passes, and `psd2-demo --help` shows the `run` and `check` commands.

- [x] 1.1 Scaffold: `pyproject.toml` (hatchling, `src/` layout, deps `httpx`, `PyJWT[crypto]`; dev extras `pytest`, `respx`; script `psd2-demo = psd2demo.cli:main`; pytest config), `.gitignore` (`config.toml`, `*.pem`, `.psd2demo/`, `exports/`, `.venv/`, `__pycache__/`), `src/psd2demo/__init__.py`, `__main__.py`, and a `cli.py` stub with `run`/`check` subcommands (`run` is the default). Test: `test_cli.py` (help exits 0; no subcommand resolves to `run`).
- [x] 1.2 `iban.py`: `normalize`, `validate` (regex, mod-97, BE length 16), `mask`. Test: `test_iban.py` (valid BE/NL/DE, bad checksum, bad length, spaces/lowercase, mask format).
- [x] 1.3 `config.py`: `Config` dataclass, `load_config(path, env, overrides)` with precedence flags > env > TOML > defaults; aggregated `ConfigError`; key file checks (exists/readable error, mode > 0600 warning); `redirect_url` check; IBAN normalize+validate; capture-mode resolution (`auto` → listener/paste; `listener` + non-loopback → error). Define the `DemoError`/`ConfigError` base in `errors.py` (app-level) here. Test: `test_config.py`.
- [x] 1.4 `config.example.toml` with every key and a comment per key giving its env var and flag.
- [x] 1.5 `transactions.py`: `Txn`, `Summary`, `normalize` (sign, counterparty side, remittance join, reference fallback, out_of_range, tolerant of missing fields), `sort_txns` (date fallback, newest first), `summarize` (per currency, `Decimal`). Test: `test_transactions.py` with fixture `tests/fixtures/transactions_p1.json`.

## Phase 2 – Enable Banking Client and `check` Command ✅
**Goal**: authenticated calls to the production API, plus a setup guide good enough to reach a passing `check`.
**Verify (user)**: follow the draft `documentation/enablebanking-setup.md` steps 1–5 (register the app, store the key, link the account, fill `config.toml`), then run `psd2-demo check`. It exits 0 and shows `[✓] CONFIG` and `[✓] BANK <your bank> (BE)`, with the bank's max consent validity.

- [x] 2.1 `eb/auth.py` `JwtSigner` (header/claims, TTL 1..86400, cache with re-sign < 60 s before expiry, injectable clock; key loaded via `cryptography` at init). Test: `test_auth.py` (generate an RSA key in the test, decode with the public key, check TTL bounds, fake-clock caching).
- [x] 2.2 `eb/models.py`: `Aspsp`, `AuthStart`, `Account`, `Session`, `SessionStatus`, `TransactionsPage` with `from_json`; `ApiSchemaError` on missing required fields. Fixtures: `aspsps.json`, `auth.json`, `session_create.json`, `session_get.json`, `transactions_p2.json`, `error_401.json`, `error_429.json`. Test: `test_models.py`.
- [x] 2.3 `eb/errors.py`: `ApiError` hierarchy (`ApiAuthError`, `ApiRateLimited`, `ApiNotFound`, `ApiServerError`, `ApiSchemaError`), built from a response (status, method, endpoint, `code`/`message`/`detail`/`error`), plus hints for 401/403/429 and `ACCESS_DENIED`/`ASPSP_ERROR`. Test: `test_errors.py`.
- [x] 2.4 `eb/client.py` `EnableBankingClient`: `_request` (bearer, accept, PSU headers on `/auth` and AIS calls, timeout), retry policy (GET: 429/5xx/transport; POST: 429 only; max 3; `Retry-After` ≤ 30 s, else 1/2/4 s; injectable sleep), `get_aspsps`, `start_auth`, `create_session`, `get_session`, `get_transactions_page`, `iter_transactions` (stop on missing/repeated key or `max_pages`, with `truncated` flag). Test: `test_client.py` with respx (request shapes, error mapping, retry cases, pagination cases).
- [x] 2.5 `log.py`: logger setup, `--verbose` DEBUG HTTP logging (method, URL, status, elapsed, body ≤ 2 KB) via httpx event hooks, `RedactingFilter` (Bearer, code, state, non-target IBANs). Test: `test_log.py`.
- [x] 2.6 `flow.py`: `Step`, `StepReporter`, `RunResult`, `run_check` (CONFIG → BANK: case-insensitive exact match; `BankNotFound` lists ≤ 10 candidates). Wire `check` in `cli.py` (load config → signer → client → run_check; exit codes 0/1/2/130). Test: `test_flow_check.py` (fake client: match, no match with candidates, API auth error → exit 1; config error → exit 2).
- [x] 2.7 Draft `documentation/enablebanking-setup.md` (R8 AC2, AC3, and the "run check" part of AC6) and `documentation/index.md`. Cover control panel signup/login; the production app form fields (Environment=Production, default private key, application name, redirect URLs: recommend an `https://` URL the user controls, or `https://localhost/callback` for paste mode, and mark `http://localhost` as unverified); privacy/terms URLs must start with `https://`; store the `<app-id>.pem` outside the repo with `chmod 600` (it cannot be downloaded again); "Activate by linking accounts" → restricted mode; fill `config.toml`; run `psd2-demo check`. No test (docs).

## Phase 3 – Consent, Session Reuse, and Transactions (`run`) ✅
**Goal**: the full production E2E run prints the last 10 days of transactions, and reruns reuse the session.
**Verify (user)**: `psd2-demo run` → the bank page opens → approve → paste the redirect URL → the table of transactions for the last 10 days and the summary appear, and it exits 0. Run `psd2-demo run` again: it shows `reused session` and no browser opens. `psd2-demo run --fresh` forces a new consent.

- [x] 3.1 `consent.py`: `new_state`, `compute_valid_until` (min(consent_days, max validity) − 60 s, UTC), `parse_callback` (`ConsentDenied`, `StateMismatch`, missing code; constant-time compare), `open_browser`. Test: `test_consent.py`.
- [x] 3.2 `consent.py` capture: `capture_paste` (daemon-thread `input`, timeout → `CallbackTimeout`), `capture_listener` (127.0.0.1 one-shot `HTTPServer` on the redirect port/path, static HTML reply that doesn't echo params, timeout). Test: `test_consent_capture.py` (inject the prompt function; listener on an ephemeral port hit from a thread; wrong path → 404 and keep waiting; timeout).
- [x] 3.3 `session_store.py`: `StoredSession` (schema_version 1, `iban_sha256`), `SessionStore.load/save/clear` (dir 0700, temp file 0600 + `os.replace`, corrupt → None + warning), and the `matches(cfg)` helper. Test: `test_session_store.py`.
- [x] 3.4 `flow.run_demo` CONSENT step: reuse when the stored session matches and `get_session` shows `AUTHORIZED`, `valid_until > now + 5 min`, and the uid is in its accounts; 401/404/other status → clear and re-consent; `--fresh` skips reuse; new consent = `start_auth` → print + open URL → capture → `parse_callback` → `create_session` → save (only after success). Test: `test_flow_run.py` (fake client + fake capture: reuse, stale → re-consent, fresh, denied, state mismatch; no file written on failure).
- [x] 3.5 `flow.run_demo` ACCOUNT step: match the normalized IBAN; `AccountNotFound` with masked list + hint. Test: added to `test_flow_run.py`.
- [x] 3.6 `flow.run_demo` TRANSACTIONS step: `date_from = today − 10 d`, `date_to = today` (injectable `today`), `iter_transactions` → normalize → sort → summarize; zero transactions = pass with an explicit message; truncated → warning. Test: added to `test_flow_run.py` (2 pages, empty, truncated).
- [x] 3.7 `output.py` `render_transactions` / `render_summary` (columns per design, terminal-width truncation, `*` out-of-range marker + legend, signed amounts with currency). Wire `run` in `cli.py` (`--fresh`, PSU headers from config: `Psu-User-Agent: psd2-demo/<version>`, `Psu-Ip-Address` if configured). Test: `test_output.py` (golden string for a fixed width), `test_cli.py` (run wiring with a mocked flow).

## Phase 4 – Export, JSON Output, and the Final Guide ✅
**Goal**: machine-readable output and a guide that takes a new user from nothing to a passing run.
**Verify (user)**: `psd2-demo run --export exports/run.json` writes a 0600 file with `meta`, `transactions`, and `raw_pages`. `psd2-demo run --json | python3 -m json.tool` is valid. A fresh read of `documentation/enablebanking-setup.md` is enough to complete setup, `check`, and `run` without outside info.

- [x] 4.1 `output.export_json` (meta with masked IBAN, normalized txns with Decimal amounts as strings, raw pages; mode 0600; parent dir created). Wire `--export`. Test: `test_output.py` (content shape, file mode).
- [x] 4.2 `--json` structured result `{ok, failed_step, steps[], summary}` to stdout for `run` and `check`; the step reporter goes quiet in JSON mode. Test: `test_cli.py` (valid JSON for both success and failure).
- [x] 4.3 Finish `documentation/enablebanking-setup.md`: the first-run walkthrough (R8 AC4) with sample output, and the troubleshooting table (R8 AC5: invalid JWT/wrong kid/clock skew, app Inactive, redirect URL not whitelisted, bank name mismatch, IBAN not in session, consent expired, 429/unattended limit, "Invalid URL" on the form), with unverified points marked (AC7).
- [x] 4.4 `README.md`: purpose, prerequisites, install, quick start (`check`, `run`), a link to the setup guide, and the test commands (`pytest`, single test `pytest tests/test_x.py::test_y`). Add the documentation entries to `documentation/index.md`.
- [x] 4.5 Update the root `CLAUDE.md` with the dev commands (venv, install, pytest, single test, run) and a short architecture note (the `eb/` client is reusable; `flow.py` orchestrates the steps).
- [x] 4.6 Final check: full `pytest` green; `grep` finds no secrets or real IBANs in the repo; the `.gitignore` coverage is confirmed.

## Verification Status
- Automated: 100 offline tests pass (`pytest`), including CLI end-to-end `run` with a mocked API (paste path, `--json`, `--export`).
- Live API: `psd2-demo check` against `api.enablebanking.com` with an unregistered key returns the mapped 403 "Application does not exist". This confirms the transport, JWT and error paths.
- Production: verified by the owner on 2026-11-08. `run` against a real Belgian bank returned the last 10 days of transactions (after fix 7).

## Design Deviations
1. `eb.errors.ApiError` does **not** subclass `DemoError` (the design tree showed it under `DemoError`). This keeps `eb/` free of app dependencies, so 001 can reuse it. `flow` catches both, and both expose `message`/`hint`.
2. ACCOUNT is a separate step after CONSENT, and the session file is saved **only after** the IBAN matches. With no match, nothing is stored and the next run re-consents.
3. `run_demo` has no `export` parameter. `cli.py` does the export (and the rendering) from `RunResult.txns`/`totals`/`raw_pages`, which keeps the flow free of output concerns. Export is reported as an extra `EXPORT` step: a write failure shows `[✗] EXPORT` and exits 1, and the table is still printed.
4. Added config `callback_timeout` (`EB_CALLBACK_TIMEOUT`, `--callback-timeout`). It is in the design's `Config` but was missing from the table.
5. `Txn` also carries `transaction_date` plus an `effective_date` property, and `Summary.net` is a computed property.
6. Network failures raise `ApiNetworkError` (its own hint, no `status=0` in the output). Models raise `ValueError` on a bad shape, and the client wraps it into `ApiSchemaError` with the real method and path. `ApiError.hint` is a property: an `ERROR_HINTS[error]` hint takes precedence over the class default.
7. **Fix after the first production run:** the bank returned 422 `PSU_HEADER_NOT_PROVIDED` because the demo sent `Psu-User-Agent` without `Psu-Ip-Address`. Enable Banking requires all of an ASPSP's `required_psu_headers` (from `GET /aspsps`) or none. `flow.select_psu_headers` now sends the full set when every required header is configured and none otherwise, and prints a note. The design's "send PSU headers when the user is present" rule is replaced by this all-or-none rule.
