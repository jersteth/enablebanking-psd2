# History document (schema v1)

Output of `enablebanking history`. The authoritative JSON Schema ships with the package (`enablebanking_psd2/schemas/account-history.v1.json`).

## Top level
`schema_version` ("1"), `generated_at`, `tool`, `range` {`date_from`, `date_to`}, `include`, `accounts[]`, `summary`.

## Account entry
- Identity: `name`, `bank`, `country`, `iban`, `currency` (the main currency).
- `status`: `ok` | `consent_required` | `rate_limited` | `error`.
- `error`: null, or {`step`, `message`, `http_status`, `api_error`, `hint`}.
- `warnings[]`: {`part`, `message`, `http_status`}. A failed optional part (balances, details), truncated paging, or serving a stale cache.
- `session_valid_until`, `fetched_at`, `truncated` (more data may exist), `cached`, `stale` (cached data served because the bank rate-limited), `api_calls` (requests this run cost).
- `transactions[]`, newest first. `balances[]` and `details` exist only when requested with `--include`.

## Transaction
`id`, `booking_date`, `value_date`, `transaction_date`, `amount`, `currency`, `counterparty_name`, `counterparty_iban`, `remittance`, `status` (`BOOK` booked, `PDNG` pending), `reference`, `out_of_range`, plus `raw` with `--include raw`.

## Rules
- **Amounts** are signed decimal strings (`"-42.50"` out, `"+2500.00"` in). Never floats; parse as a decimal.
- **Dates** are `YYYY-MM-DD`; timestamps are UTC `...Z` (except `session_valid_until`).
- **Missing values** are `null`; keys are never dropped.
- **`id`** is stable across runs: `ref:<bank reference>`, else `h:<hash>`. Two identical same-day payments are told apart only by their order in the bank's response.
- **`out_of_range: true`**: the bank returned it outside the requested period; usually exclude it from period totals.
- **`summary.accounts`** counts accounts per status. **`summary.totals`** is per currency (`count`, `credits`, `debits`, `net`) over `ok` accounts only.

## Exit codes
0 every account `ok` · 3 partial (some `ok`) · 1 no account `ok` · 2 config or usage error (no document) · 130 interrupted.

## Limits
Banks may allow only 4 unattended requests per account per day; results are cached (default 60 minutes). Banks may limit history to about 90 days without a fresh login.
