# 001 – Account Access Flow: Requirements

> **Superseded by [003-accounts-and-sessions](../003-accounts-and-sessions/requirements.md).** Kept for history; do not implement.

## Introduction
An end-to-end PSD2 account information (AIS) flow against the Enable Banking API: authenticate the application, choose a bank, get user consent through the bank redirect, set up a session, and read accounts, balances and transactions. It must work in the Enable Banking sandbox (Mock ASPSP) and in production restricted mode (the owner's own linked Belgian accounts).

## Alignment with Product Vision
Covers every capability in `specs/product.md` (bank discovery, consent/authorization, AIS, session lifecycle, sandbox/production) for the smallest useful scope: one user, the owner, accessing their own personal accounts. Payment initiation and business accounts stay out of scope.

## Enable Banking API Reference (facts relied on)
- Base URL: `https://api.enablebanking.com` (same for sandbox and production; the environment is set by the registered application).
- Every request uses `Authorization: Bearer <JWT>`. The JWT is RS256-signed with the application's private key. Header: `typ=JWT`, `alg=RS256`, `kid=<application id>`. Claims: `iss="enablebanking.com"`, `aud="api.enablebanking.com"`, `iat`, `exp`. Max lifetime is 86400 s.
- `GET /aspsps` – list banks; filter by `country`, `psu_type`, `service`.
- `POST /auth` – body includes `access.valid_until` (RFC3339), `aspsp {name, country}`, `state`, `redirect_url`, `psu_type`. Returns the bank redirect `url` and `authorization_id`.
- The bank redirects to `redirect_url?code=...&state=...` (or `error`/`error_description` on failure).
- `POST /sessions` with `{code}` returns `session_id`, the authorized `accounts`, and `access` validity.
- `GET /sessions/{id}`, `DELETE /sessions/{id}` (revokes the consent).
- `GET /accounts/{uid}/details`, `/balances`, `/transactions` (`date_from`, `date_to`, `continuation_key` pagination).
- Optional PSU context headers: `Psu-Ip-Address`, `Psu-User-Agent`, etc.
- Production restricted mode: only accounts linked to the application in the Enable Banking control panel are reachable. It is allowed for personal non-commercial use.
- Sandbox Mock ASPSP returns transactions in fixed batches of 10. Date filtering is not reliable across sandboxes.

## Requirements

### R1 – Configuration
**As** the owner, **I want** to configure the client with my Enable Banking application credentials, **so that** it can authenticate against the API.
- AC1: Configuration supplies application ID, path to the private key (PEM), redirect URL, and default country (`BE`).
- AC2: Several profiles (at least `sandbox` and `production`) can be configured, and one is chosen per run.
- AC3: Values come from a config file and/or environment variables. Secrets are never stored in the repository.
- AC4: Missing or invalid configuration (unreadable key, missing app ID) stops the client before any API call, with a message naming the missing item.

### R2 – API Authentication
**As** the client, **I want** to generate valid JWTs, **so that** every API request is authenticated.
- AC1: Builds the JWT with the header and claims listed above, signed RS256 with the configured key.
- AC2: Token lifetime is configurable, capped at 86400 s. A cached token is reused until shortly before it expires.
- AC3: An API 401/403 is reported as an authentication error and includes the API's error body.

### R3 – Bank Discovery
**As** the owner, **I want** to list the banks available for Belgian personal accounts, **so that** I can choose mine.
- AC1: Calls `GET /aspsps` with `country` (default `BE`) and `psu_type=personal`.
- AC2: Shows for each bank: name, country, and maximum consent validity (when provided).
- AC3: Supports filtering the list by name substring.
- AC4: In the sandbox profile, the Mock ASPSP can be chosen even if it is not in `BE`.

### R4 – Authorization (Consent)
**As** the owner, **I want** to start a consent with a chosen bank and complete it in my browser, **so that** the client gets access to my accounts.
- AC1: Calls `POST /auth` with the chosen bank, `psu_type=personal`, the configured `redirect_url`, a random unguessable `state`, and `valid_until` = now + requested duration. The duration defaults to the bank's maximum consent validity and must never exceed it.
- AC2: Shows the returned bank URL and opens it in the default browser where possible.
- AC3: Gets the callback `code` in one of two ways: (a) a temporary local HTTP listener when `redirect_url` points to localhost, or (b) the user pastes the full redirect URL.
- AC4: Rejects a callback whose `state` does not match the one it generated.
- AC5: A callback containing `error` is reported with `error` and `error_description`, and no session is created.
- AC6: Exchanges `code` via `POST /sessions` and stores the session (ID, bank, accounts, access validity, creation time).

### R5 – Session Management
**As** the owner, **I want** to list, inspect and revoke sessions, **so that** I can manage consent lifecycle.
- AC1: Lists stored sessions with bank, number of accounts, `valid_until`, and status (active / expired / revoked).
- AC2: Refreshes a session's status and accounts from `GET /sessions/{id}`.
- AC3: Revokes a session via `DELETE /sessions/{id}` and marks it revoked locally.
- AC4: Any operation on an expired or revoked session fails clearly and suggests re-authorizing (R4).
- AC5: Stored sessions survive restarts of the client.

### R6 – Account Data
**As** the owner, **I want** to read details, balances and transactions of my accounts, **so that** I can verify PSD2 data access end to end.
- AC1: Lists the accounts of a session, showing account UID, IBAN (when present), name, and currency.
- AC2: Shows account details (`/details`).
- AC3: Shows all balances returned (`/balances`), with balance type, amount, currency, and reference date.
- AC4: Fetches transactions over a date range (`date_from`/`date_to`, default the last 90 days) and follows `continuation_key` until exhausted or until an optional maximum count is reached.
- AC5: Shows for each transaction: booking date, amount with sign (credit/debit), currency, counterparty name, remittance information, and status (booked/pending).
- AC6: Can export transactions and balances as JSON. Raw API responses can be dumped for debugging.

### R7 – Error Handling and Diagnostics
**As** the owner, **I want** clear API error reporting, **so that** I can diagnose bank/aggregator issues.
- AC1: Every non-2xx response shows HTTP status, endpoint, and the API's error fields (e.g. `code`, `message`, `detail`).
- AC2: Rate-limit responses (429) are reported as such. Retries are bounded, with backoff.
- AC3: A verbose mode logs requests and responses with the JWT and other secrets redacted.

## Non-Functional Requirements

### Architecture
- Interface: a command-line tool with one subcommand per capability (config check, list banks, authorize, sessions, accounts, balances, transactions).
- The Enable Banking API client layer is kept separate from the CLI/presentation layer so it can be reused (e.g. by a later UI).
- Local storage of sessions is simple and file-based. No database server.

### Security
- The private key is only read from the configured path. It is never logged, copied, or committed. Key files, local session storage, and config with secrets are git-ignored.
- `state` is generated with a cryptographically secure RNG.
- The local callback listener binds only to the loopback address, accepts a single callback, and shuts down right after (or after a timeout).
- Session storage file permissions are restricted to the current user.
- Logs never contain the JWT, private key material, or authorization `code`.

### Reliability
- Network timeouts are set on all HTTP calls.
- Transaction pagination always ends: stop on missing/repeated `continuation_key` or when the max count is reached.
- Failures partway through the flow (e.g. browser closed, callback timeout) leave no half-written session.

### Usability
- Defaults favour the main use case: country `BE`, `psu_type=personal`, longest allowed consent.
- Output is human-readable tables by default. A `--json` flag gives machine-readable output.
- Each command's help text shows usage and examples.

### Testability
- The API client can be unit-tested against recorded/mocked HTTP responses without network access.
- The full flow can be run manually against the sandbox Mock ASPSP before trying production.
