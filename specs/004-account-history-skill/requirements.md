# 004 – Account History Skill: Requirements

## Introduction
Turns the demo into a repeatable, non-interactive capability: fetch the transaction history of **all configured accounts** for a configurable number of days and detail level, and emit one stable, versioned JSON document. It is exposed three ways, all over one core:
1. a headless CLI command, for any application or scheduler
2. a Python API, for Python applications
3. a Claude Code skill, so an agent can fetch and use the data on request

Bank consent stays interactive: it is a separate `authorize` step done once per bank. The skill itself never prompts.

## Alignment with Product Vision
`specs/product.md` targets testing and using PSD2 access to the owner's own Belgian accounts through Enable Banking. This feature turns the verified flow (002) into a reusable data source for other tools (budgeting, reporting, agents), without screen scraping and without sharing credentials. Scope stays one owner, personal accounts, AIS only, production restricted mode.

## Dependencies
- **002** (implemented): `eb/` client, JWT, PSU all-or-none, consent capture, IBAN handling, pagination, redaction.
- **003 R1, R2, R5 (details), R6 (date range)**: multiple `[accounts.<name>]`, a multi-session store keyed per bank, account details, and date-range validation. If 003 is not yet implemented, these parts are built first as part of this spec, exactly as 003 specifies them. 004 must not define a second config or store format.

## Constraints from PSD2 / Enable Banking
- Getting consent needs the account holder at the bank (redirect plus strong authentication), so unattended runs can only use **existing** valid sessions.
- Unattended calls carry no PSU headers, or the full set if configured (002 rule). Without PSU presence, banks may cap AIS access at **4 calls per day per account** (PSD2 RTS art. 36(5)). One account fetch uses several calls: transactions (one per page), plus details and balances when requested.
- Banks may limit the reachable history (often ~90 days) without a fresh SCA, and consent validity is capped by the bank's `maximum_consent_validity`.

## Requirements

### R1 – Authorize Accounts for Unattended Use
**As** the owner, **I want** to authorize each bank once with a long consent, **so that** later fetches run without me.
- AC1: `psd2-demo authorize [--account NAME | --all]` runs the 002/003 consent flow interactively for the selected accounts' banks, one consent per bank covering all its configured accounts.
- AC2: The consent length defaults to the bank's `maximum_consent_validity`, overridable with `--consent-days`. Afterwards it prints each account's status and the `valid_until`.
- AC3: Banks whose stored session is still valid (more than 7 days left) are skipped unless `--fresh` is given.
- AC4: `psd2-demo authorize --status` (no API calls beyond `GET /sessions/{id}`) lists per account: authorized / expiring (< 7 days) / consent_required.

### R2 – Headless History Fetch
**As** an application, **I want** one non-interactive command that fetches history for all accounts, **so that** I can run it on a schedule.
- AC1: `psd2-demo history [--account NAME ...] [--days N | --from D [--to D]] [--include balances,details,raw] [--output FILE]`. With no `--account`, all configured accounts are fetched.
- AC2: `--days` defaults to `history_days` from the config (`[history]` section), else 30. The range is validated as in 003 R6. One range applies to all accounts.
- AC3: Never prompts, never opens a browser, never starts a consent. An account without a valid session gets the status `consent_required` and is skipped.
- AC4: Accounts are processed independently: one account's failure does not stop the others. The order is the config order.
- AC5: The JSON document goes to stdout, or to `--output FILE` (written atomically, mode 0600). Progress and diagnostics go to stderr only, and only with `--verbose`. stdout carries nothing but the JSON.
- AC6: Exit codes:
  - `0`: every requested account is `ok`
  - `3`: partial (at least one `ok` and at least one not)
  - `1`: no account `ok`
  - `2`: config or usage error (no JSON document, an error on stderr)
  - `130`: interrupted

  For codes 0, 1 and 3 a complete JSON document is always written.

### R3 – Detail Levels
**As** an application, **I want** to choose how much data is fetched, **so that** I control API usage and payload size.
- AC1: The default is normalized transactions only (002 `Txn` fields, plus `counterparty_iban` and `transaction_date`).
- AC2: `--include balances` adds `GET /balances` (003 R4 fields). `--include details` adds `GET /details` (003 R5 fields). `--include raw` adds each transaction's raw API object under `raw`.
- AC3: Defaults come from `history_include` in the config. The CLI flag replaces the config value, it does not merge with it.
- AC4: A failing optional part (balances or details) sets that part to `null` and records an entry in the account's `warnings`. The account stays `ok` if the transactions succeeded.

### R4 – Stable JSON Output Contract
**As** an application developer, **I want** a documented, versioned schema, **so that** my integration doesn't break silently.
- AC1: The top level has:
  - `schema_version` (`"1"`), `generated_at` (UTC ISO 8601), `tool` (name and version)
  - `range` (`date_from`, `date_to`), `include`
  - `accounts[]`
  - `summary` (counts per status; totals per currency over the `ok` accounts)
- AC2: Each entry in `accounts[]` has:
  - `name`, `bank`, `country`, `iban` (full: the owner's own configured account), `currency`
  - `status`: `ok`, `consent_required`, `rate_limited` or `error`
  - `error` (`null`, or `{step, message, http_status, api_error, hint}`) and `warnings[]`
  - `session_valid_until`, `fetched_at`, `truncated`
  - `transactions[]`, plus `balances` and `details` when included
- AC3: In each transaction:
  - Amounts are decimal strings with a sign (`"-42.50"`), never floats. Dates are `YYYY-MM-DD`. Missing values are `null`; keys are never dropped.
  - Transactions are sorted newest first. Out-of-range rows are kept, with `out_of_range: true`.
  - Each has a stable `id`: the bank's `entry_reference`, else `transaction_id`, else a SHA-256 of `(account, booking/value date, amount, currency, counterparty, remittance)`. Repeated fetches of the same transaction yield the same `id`, so applications can de-duplicate.
- AC4: A JSON Schema file (`schemas/account-history.v1.json`) is committed. Tests validate the real output against it. Breaking changes require `schema_version` `"2"` and a new schema file.

### R5 – Python API
**As** a Python application, **I want** to call the fetch directly, **so that** I don't have to spawn a process.
- AC1: `psd2demo.history.fetch_history(config_path=None, accounts=None, days=None, date_from=None, date_to=None, include=None) -> dict` returns the same document as the CLI (same schema, same statuses).
- AC2: It never prompts, prints nothing to stdout, and raises only `ConfigError` (and `KeyboardInterrupt`). All per-account problems are reported inside the document.
- AC3: The CLI `history` command is a thin wrapper over this function.

### R6 – Rate-Limit Protection and Caching
**As** the owner, **I want** repeated runs not to exhaust the bank's unattended-access limit, **so that** the skill keeps working throughout the day.
- AC1: Each successful account fetch is cached locally per `(account, range, include)` under `.psd2demo/cache/` (0600 files, git-ignored), with `fetched_at`.
- AC2: `--max-age MINUTES` (config `history_max_age`, default 60) serves a cached result when it is fresh enough, and makes no API calls for that account. The output marks it `"cached": true`.
- AC3: `--no-cache` forces API calls. A 429 response sets `rate_limited` with the hint, and falls back to a cache entry of any age if one exists (marked `cached: true`, `stale: true`, plus a warning).
- AC4: Each account counts its API calls, and they are reported in the output (`api_calls`) so applications can budget them.

### R7 – Claude Code Skill
**As** the owner using Claude Code, **I want** a skill that fetches my account history, **so that** I can ask an agent for transactions or summaries in plain language.
- AC1: `.claude/skills/bank-account-history/SKILL.md`, with frontmatter (`name`, `description` saying when to use it: requests for bank transactions, balances or spending over a period, for the configured Belgian accounts). Committed in the repo.
- AC2: The instructions tell the agent to:
  - run `psd2-demo history` with the right `--days`/`--account`/`--include` from the request, with `--output` to a file under `exports/`
  - read the JSON from that file
  - report by account status: for `consent_required`, tell the user to run `psd2-demo authorize --account NAME` themselves (it's interactive), and never attempt it
  - prefer the cache (default `--max-age`) and avoid repeated fetches in one conversation
- AC3: Data handling in the instructions: answer from the JSON file without echoing raw full IBANs or whole transaction lists unless asked, and never send the data to external services.
- AC4: The skill works from the repo root with the project's `.venv` (the command path is stated in the skill). It includes one worked example request and the matching command.

### R8 – Documentation
**As** a user or integrator, **I want** clear docs, **so that** I can set up unattended fetching correctly.
- AC1: `documentation/account-history.md`, linked from `documentation/index.md` and the README, covers:
  - the authorize-once workflow
  - `history` usage, detail levels and exit codes
  - the JSON contract with an example document
  - caching and the 4/day limit
  - a cron/systemd timer example
  - the Python API example
  - how to use the Claude Code skill
- AC2: `config.example.toml` documents the `[history]` keys (`history_days`, `history_include`, `history_max_age`) and multiple `[accounts.*]`.

## Non-Functional Requirements

### Architecture
- One core module (`history.py`) builds the document. The CLI command, Python API and skill all go through it. There is no separate fetch logic.
- The core reuses the `eb/` client, the 003 session store and account selection, and the 002 normalization. Session lookup is read-only: `history` never writes or clears sessions. Only `authorize` (and 002/003 interactive commands) do.
- Serialization lives in one place (output contract). The schema version is a constant used by both the code and the tests.

### Performance
- Accounts are fetched sequentially, which is gentle on bank limits. Pagination is bounded by the 002 page cap.
- A run with all accounts cached does no network I/O beyond nothing at all. Not even `GET /aspsps` is called: the bank metadata comes from the store or cache.

### Security
- The output contains full IBANs and transaction data. Files are written as 0600, and stdout is the default sink. The docs warn against committing exports. `exports/` and `.psd2demo/cache/` are git-ignored.
- Logs still redact secrets (002). The skill instructions forbid sending data to external services or printing more than needed.
- No consent, revocation or other state-changing API call happens in `history` or the skill.

### Reliability
- Per-account isolation (R2 AC4). An unexpected exception for one account becomes `status: error` with `step` set, not a crash.
- Output is deterministic for identical API responses (ordering, ids, key order), which lets applications diff runs.
- A truncated pagination run sets `truncated: true` plus a warning, never a silent partial list.

### Usability
- Zero-argument `psd2-demo history` works after `authorize`, using the config defaults.
- Error hints name the exact next command (`psd2-demo authorize --account NAME`).

### Testability
- Offline tests: the document builder against fakes (all statuses, partial runs, cache hit/stale/miss, rate-limit fallback, optional-part failure), JSON Schema validation of the real output, stable-`id` determinism, CLI exit codes, stdout purity (only JSON), and the Python API with no stdout output.
- Manual production check: `authorize --all`, `history --days 30 --include balances,details`, a rerun served from the cache, and the skill invoked from Claude Code with a natural-language request.
