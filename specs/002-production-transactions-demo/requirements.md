# 002 – Production Transactions Demo: Requirements

## Introduction
A single-command, end-to-end demo against Enable Banking **production** in restricted mode. For one bank and one account that the owner specifies, it gets consent, finds the account, and retrieves and shows transaction details for the last 10 days. It proves real PSD2 access to a Belgian personal account works, with as few moving parts as possible.

## Alignment with Product Vision
Covers the core capabilities in `specs/product.md` (consent/authorization, AIS transaction access, session lifecycle) on a real Belgian bank. It is a narrow, demo-sized slice of `001-account-access-flow`: no bank discovery UI, no multi-session management, no sandbox profile. Code written here must be reusable by 001 (shared API client).

## Prerequisites (outside the app; documented by R8)
- An Enable Banking **production** application is registered. Its private key (PEM) is available locally, and its redirect URL is whitelisted.
- The target account is **linked** to the application in the Enable Banking control panel, which puts the application in restricted mode. Restricted mode can only reach linked accounts and is allowed for personal non-commercial use.

## Enable Banking Setup Facts (from docs; drive R8)
- Control panel: `https://enablebanking.com/cp/applications`. Sign up and log in, then use "Add a new application".
- Form: **Environment** = Production; **Private key** = default (generated in the browser); **Application name** (shown to the user at bank authorization); **Redirect URLs** (whitelist). Optional privacy-policy/terms URLs must be full `https://` URLs, otherwise the form returns "Invalid URL" (reported by a third-party guide, unverified).
- After "Register", the browser downloads `<application-id>.pem`. The file name is the application ID (the JWT `kid`). The key cannot be downloaded again, so losing it means registering a new application.
- A new production app starts as **Inactive**. "Activate by linking accounts" runs a bank authorization in the control panel. Once the link is confirmed, the app is active in **restricted mode**.
- Linking does not replace API authorization: the demo must still run its own `POST /auth` consent.
- Redirect URL scheme rules are not fully documented. Third-party guides use `https://` URLs, and whether `http://localhost` is accepted is unverified. The demo must therefore work with any whitelisted `https` URL (see R3 AC3).

## Enable Banking API Usage
- Auth: `Authorization: Bearer <JWT>`. RS256. Header `typ=JWT`, `alg=RS256`, `kid=<application id>`. Claims `iss="enablebanking.com"`, `aud="api.enablebanking.com"`, `iat`, `exp` (lifetime ≤ 86400 s). Base URL `https://api.enablebanking.com`.
- `GET /aspsps?country=BE&psu_type=personal` – check the bank and read its maximum consent validity.
- `POST /auth` – `{access: {valid_until}, aspsp: {name, country}, state, redirect_url, psu_type: "personal"}`. Returns the bank `url`.
- Callback: `redirect_url?code=…&state=…`, or `error`/`error_description`.
- `POST /sessions` `{code}` – returns `session_id`, `accounts[]` (each with `uid` and `account_id.iban`, among others), and `access.valid_until`.
- `GET /sessions/{id}` – check that a stored session is still valid.
- `GET /accounts/{uid}/transactions?date_from=…&date_to=…[&continuation_key=…]`.

## Requirements

### R1 – Demo Input
**As** the owner, **I want** to give the bank and account to test, **so that** the demo targets my real account.
- AC1: Inputs: application ID, private key path, redirect URL, bank name, bank country (default `BE`), and account IBAN.
- AC2: Inputs come from a local config file and/or environment variables. CLI flags override them.
- AC3: The IBAN is normalized (spaces removed, uppercase) and checked for format (country prefix plus mod-97 checksum) before any API call.
- AC4: A missing or invalid input stops the demo before any API call, with a message naming the problem.

### R2 – Bank Validation
**As** the owner, **I want** the demo to confirm my bank is supported, **so that** typos fail early.
- AC1: Looks up the bank name (case-insensitive exact match) in `GET /aspsps` for the given country and `psu_type=personal`.
- AC2: If there is no match, lists close matches (substring) and stops.
- AC3: Reads the bank's maximum consent validity for use in R3.

### R3 – Consent
**As** the owner, **I want** to approve access at my bank in the browser, **so that** the demo gets a session.
- AC1: Calls `POST /auth` with a cryptographically random `state`. `valid_until` is the lower of a configured duration (default 1 day) and the bank's maximum.
- AC2: Opens the bank URL in the default browser and also prints it.
- AC3: Default capture mode is **paste**. The user approves at the bank, the browser lands on the whitelisted redirect URL (the page may fail to load, which is fine), and the user pastes the full URL from the address bar. Optional **listener** mode: a one-shot loopback HTTP listener, used only when `redirect_url` is `http://localhost:<port>/…` or `http://127.0.0.1:<port>/…`. The wait times out (default 5 min).
- AC4: Rejects a `state` mismatch. Reports a callback `error` with its description and stops.
- AC5: Exchanges `code` via `POST /sessions`.

### R4 – Session Reuse
**As** the owner, **I want** repeat runs to skip re-consent while the consent is valid, **so that** I can rerun the demo quickly.
- AC1: Stores session ID, bank, account UID, and `valid_until` locally after R3.
- AC2: On the next run with the same bank and IBAN, checks the stored session with `GET /sessions/{id}`. If it is still authorized and not expired, R3 is skipped.
- AC3: A `--fresh` flag forces a new consent.
- AC4: Invalid, expired, or revoked stored sessions are discarded, and R3 runs.

### R5 – Account Selection
**As** the owner, **I want** the demo to use exactly the account I gave, **so that** results are for the right account.
- AC1: Selects the session account whose IBAN matches the input IBAN (after normalization).
- AC2: If no account matches, lists the session's accounts (masked IBAN, name, currency) and stops with a hint to check the account link in the control panel or the account selection at the bank.

### R6 – Transactions (Last 10 Days)
**As** the owner, **I want** the transactions of the last 10 days, **so that** I can see real PSD2 data.
- AC1: `date_from` = today − 10 days and `date_to` = today, using the local date in ISO format (`YYYY-MM-DD`).
- AC2: Follows `continuation_key` until it is absent. Stops if a key repeats or a hard page cap is reached.
- AC3: For each transaction, shows: booking date, value date, amount with sign (from `credit_debit_indicator`), currency, counterparty name and IBAN (if present), remittance information, status (booked/pending), and the bank's transaction reference (if present).
- AC4: Sorts by booking date, newest first. Shows a summary: count, total credits, total debits, and net, per currency.
- AC5: Transactions the bank returns outside the requested range are still shown, but flagged.
- AC6: `--export <file>` writes the normalized transactions plus the raw API pages to a file.

### R7 – End-to-End Result
**As** the owner, **I want** a clear pass/fail outcome, **so that** the demo works as an end-to-end test.
- AC1: Prints each step (config → bank → consent/reuse → account → transactions) with its status.
- AC2: Exits 0 when all steps succeed (zero transactions is a valid pass, reported explicitly). Exits non-zero, naming the failed step, otherwise.
- AC3: API errors show HTTP status, endpoint, and the API error body.

### R8 – Setup Guide
**As** the owner, **I want** step-by-step instructions to set up Enable Banking and the demo, **so that** I can go from nothing to a successful run without guessing.
- AC1: Write `documentation/enablebanking-setup.md` and link it from `documentation/index.md` and the README.
- AC2: Cover, in order: create an account / log in to the control panel; register a production app (each form field and the value to enter, including redirect URL choice and its effect on capture mode); store the downloaded `<application-id>.pem` (location outside the repo, permissions `chmod 600`, note that it cannot be downloaded again); activate by linking accounts (choose the target bank and account; the app becomes active in restricted mode); find the exact bank name to configure (via the demo's bank check or `GET /aspsps`).
- AC3: Cover demo configuration: a config file template (`config.example.*`, committed) mapping each R1 input to where it comes from, plus the equivalent environment variables.
- AC4: Cover the first run: the command, what happens in the browser, how to paste the redirect URL, and what a successful output looks like.
- AC5: A troubleshooting section that maps common failures to fixes: invalid JWT / wrong `kid` / clock skew; app still Inactive; redirect URL not whitelisted; bank not found (name mismatch); IBAN not in session (account not linked or not selected at the bank); consent expired; 429 / unattended-access limit; "Invalid URL" on the registration form.
- AC6: The demo provides a `check` subcommand. It validates config and key, signs a JWT, and calls `GET /aspsps`, so the setup can be verified without starting a consent. The guide tells the user to run it after setup.
- AC7: Every unverified point (e.g. whether `http://localhost` redirect URLs are accepted) is marked as such in the guide.

## Non-Functional Requirements

### Architecture
- One CLI entry point (e.g. `demo`) runs the whole flow, plus a `check` subcommand (R8 AC6).
- The Enable Banking API client (JWT, HTTP, typed responses) is a separate module with no CLI/IO concerns, so `001-account-access-flow` can reuse it.
- Session storage is a single local file.

### Security
- The private key is read from the configured path only, and never logged or copied.
- The JWT, authorization `code`, and full IBANs of other accounts never appear in logs. Verbose mode redacts them.
- The local session file is user-only readable (0600). Config with secrets, key files, session files, and JSON exports are git-ignored.
- The callback listener binds to `127.0.0.1` only, accepts one request, and then shuts down.

### Reliability
- Every HTTP call has a timeout. HTTP 429 and 5xx are retried at most 3 times with backoff, honouring `Retry-After`.
- Sends `Psu-Ip-Address` and `Psu-User-Agent` when the user is present (during and right after consent). Banks may limit unattended AIS calls (PSD2 RTS: 4 per day without the PSU present).
- A failure partway through the flow never leaves a half-written session file (write to a temp file, then rename).

### Usability
- Default run: `demo` with config in place, no arguments → bank login in the browser → table of transactions.
- Human-readable table output. Amounts are formatted with currency.

### Testability
- The API client and the transaction normalization/summary logic are unit-tested against fixture JSON, without network access.
- The production run is manual, since it needs a real bank login. No automated test calls production.
