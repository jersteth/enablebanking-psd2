# 003 – Accounts and Sessions: Requirements

Supersedes `001-account-access-flow`. It covers only what 001 asked for and 002 did not deliver.

## Introduction
This extends the production-verified 002 demo into a small personal AIS tool:
- list banks
- read balances and account details as well as transactions
- choose any date range
- configure several accounts (possibly at different banks)
- list and revoke consents

All of it reuses the 002 building blocks: the `eb/` client, config, consent capture, the all-or-none PSU headers, the step reporter and the exit codes.

## Alignment with Product Vision
Completes the capabilities in `specs/product.md`: bank discovery, AIS (balances, details, transactions), and session lifecycle, including revocation. It stays within the product's scope: one owner, personal Belgian accounts, production restricted mode.

Out of scope:
- payment initiation (PIS)
- business accounts
- the sandbox profile (production restricted mode is verified and enough)
- CSV export
- any UI beyond the CLI

## Already Delivered by 002 (do not re-specify)
JWT auth; `GET /aspsps` bank match; consent with paste/listener capture and state check; session reuse; IBAN match; paginated transactions for the last 10 days; table/JSON output; `--export`; `check`; redaction; all-or-none PSU headers driven by `required_psu_headers`.

## Enable Banking API Usage (new)
- `GET /accounts/{uid}/balances` → `{balances: [{name, balance_amount: {currency, amount}, balance_type, reference_date, last_change_date_time, last_committed_transaction}]}`. `balance_type` is an ISO 20022 code (e.g. `CLBD`, `ITAV`, `XPCD`, `CLAV`).
- `GET /accounts/{uid}/details` → an account resource (`account_id.iban`, `name`, `product`, `currency`, `cash_account_type`, `usage`, `account_servicer`, `details`, among others). Fields vary by bank.
- `DELETE /sessions/{id}` revokes the session and closes the consent at the bank.
- ASPSP fields used for listing: `name`, `country`, `bic`, `maximum_consent_validity`, `required_psu_headers`, `beta`.
- PSU headers apply to the balances, details and transactions calls, all-or-none (as in 002).

## Requirements

### R1 – Multiple Configured Accounts
**As** the owner, **I want** to configure several named accounts, **so that** I can switch between them without editing config.
- AC1: The TOML supports `[accounts.<name>]` tables with `bank_name`, `bank_country` (default `BE`), and `iban`. Each IBAN is validated as in 002.
- AC2: `--account <name>` selects one. Without it: `default_account` from the TOML, else the only configured account, else a `ConfigError` listing the names.
- AC3: The 002 `[demo]` section (and `EB_BANK_NAME`/`EB_IBAN`/`--bank`/`--iban`) still works. It acts as an account named `demo`. `--bank`/`--iban` flags define an ad-hoc account that overrides the selection.
- AC4: Duplicate IBANs or unknown `--account` names are config errors (exit 2).

### R2 – Multi-Session Store
**As** the owner, **I want** one stored consent per bank, reused across commands and accounts, **so that** I authorize each bank as rarely as possible.
- AC1: The store holds multiple session records keyed by `(app_id, bank_name, bank_country)`. Each record lists every authorized account as `(uid, iban_sha256)`, its `valid_until`, its `created_at`, and its status (`active`/`revoked`).
- AC2: A command for account X reuses its bank's session if X's IBAN is among the session's accounts and the 002 validity checks pass (AUTHORIZED, more than 5 minutes left, uid present). Otherwise it runs a new consent for that bank, replacing that bank's record only.
- AC3: A 002 schema v1 file is migrated on first load, with no re-consent needed while it is still valid. Writes stay atomic and 0600.
- AC4: `--fresh` forces a new consent for the selected account's bank only.

### R3 – `banks` Command
**As** the owner, **I want** to list the available banks, **so that** I can find the exact name to configure.
- AC1: `psd2-demo banks [--country BE] [--filter TEXT]` lists personal-PSU banks: name, BIC, max consent validity (days), required PSU headers, and a beta flag.
- AC2: Sorted by name. The filter is a case-insensitive substring match.
- AC3: Needs only app credentials, no account. Works without any account configured. Supports `--json`.

### R4 – `balances` Command
**As** the owner, **I want** my account's balances, **so that** I see the current position.
- AC1: `psd2-demo balances [--account NAME]` runs BANK → CONSENT → ACCOUNT → BALANCES.
- AC2: Shows each balance's type code with a readable label for common codes (`CLBD` closing booked, `ITAV` interim available, `ITBD` interim booked, `XPCD` expected, `CLAV` closing available, `OPBD` opening booked, `FWAV` forward available). Unknown codes are shown as-is. Also shows the name, the signed amount with currency, the reference date, and the last change time.
- AC3: Zero balances returned is a pass, reported explicitly. Supports `--json` and `--export`.

### R5 – `accounts` Command
**As** the owner, **I want** to see the accounts a consent covers and their details, **so that** I can verify what I authorized.
- AC1: `psd2-demo accounts [--account NAME]` ensures a session for the selected account's bank. It then lists all accounts in that session: masked IBAN (the selected account marked, and shown unmasked), name/product, currency, and cash account type.
- AC2: `--details` also calls `GET /accounts/{uid}/details` for the selected account and prints every non-empty field returned (nested objects flattened as `a.b`).
- AC3: Supports `--json`. In JSON, IBANs other than the selected account's are masked.

### R6 – Transaction Date Range
**As** the owner, **I want** to choose the period, **so that** I am not limited to 10 days.
- AC1: `run` (alias `transactions`) accepts `--days N` (N ≥ 1) or `--from YYYY-MM-DD [--to YYYY-MM-DD]`. These are mutually exclusive. The default stays 10 days. `--to` defaults to today.
- AC2: `from > to` or a future `to` is a usage error (exit 2).
- AC3: If the bank rejects the range (e.g. history beyond its limit, often 90 days without a new SCA), the error is shown with a hint to shorten the range or re-consent with `--fresh`.
- AC4: The range is shown in the TRANSACTIONS step line, in the export meta, and in the JSON summary (as in 002).

### R7 – `sessions` Command
**As** the owner, **I want** to list and revoke consents, **so that** I control which access stays open.
- AC1: `psd2-demo sessions` lists stored sessions: bank, country, number of accounts (masked IBANs of configured ones), `valid_until`, local status, and live status from `GET /sessions/{id}` (`--offline` skips the API call).
- AC2: `psd2-demo sessions revoke (--account NAME | --bank NAME | --all)` calls `DELETE /sessions/{id}` and marks the record revoked. A 404 or an already-closed session counts as success and is reported. Other API errors leave the record unchanged and exit 1.
- AC3: `--all` asks for confirmation unless `--yes` is given.
- AC4: `sessions prune` deletes local records that are revoked or expired, without API calls.

## Non-Functional Requirements

### Architecture
- New endpoints (`get_balances`, `get_account_details`, `delete_session`) go in `eb/client.py` with typed models. `eb/` still has no app imports.
- Every data command shares one "ensure session + select account" path in `flow.py`. No per-command copies of the consent logic.
- Each subcommand is a thin function: config → flow steps → renderer. The output functions live in `output.py`.

### Security
- Revocation is the only destructive API call. It is never implicit (no auto-revoke on errors or `--fresh`).
- The session store still keeps only IBAN hashes. Account listings mask IBANs except the selected account's.
- 002's redaction, file modes and git-ignore rules apply unchanged.

### Reliability
- `DELETE` is not retried except on 429, as for other non-GET calls.
- Store migration is lossless and idempotent. An unreadable store is reported and ignored, never overwritten silently with a half record.

### Usability
- `run` with 002-style config behaves exactly as before.
- Every command supports `--json` and exits 0/1/2/130 as in 002. `--help` gives examples for each command.
- The setup guide and README document the new commands and the `[accounts.*]` config.

### Testability
- All new behavior is covered by offline tests: respx for the client, fakes for the flow, and CLI end-to-end tests with a mocked API.
- The manual production check covers `banks`, `balances`, `accounts --details`, `run --days 30`, `sessions`, and `sessions revoke --account`.
