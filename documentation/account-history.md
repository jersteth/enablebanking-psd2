# Account History (unattended JSON export)

`psd2-demo history` fetches the transaction history of **all configured accounts** without any interaction and writes one versioned JSON document. Applications run it on a schedule, Python code calls `fetch_history()`, and Claude Code uses it through the `bank-account-history` skill. Setup (Enable Banking app, accounts) is in [enablebanking-setup.md](./enablebanking-setup.md).

## 1. Authorize once per bank
Getting a PSD2 consent needs you at your bank (login plus strong authentication), so `history` can only use consents that already exist. Create them interactively:

```bash
psd2-demo authorize            # every configured account; one bank login per bank
psd2-demo authorize --status   # authorized / expiring (< 7 days) / consent_required per account
```

- One consent per bank covers all your configured accounts at that bank. Select all of them on the bank's consent screen.
- The consent lasts the bank's maximum (typically 180 days). `--consent-days N` sets a shorter one.
- Banks whose stored consent covers their accounts for more than 7 more days are skipped. `--fresh` renews anyway.
- An account missing from the consent is reported as `missing`, with what to do: select it at the bank, or link it in the Enable Banking control panel.

Renew before expiry: run `psd2-demo authorize --status` weekly (see the cron example), and `psd2-demo authorize` when something shows `expiring`.

## 2. Fetch
```bash
psd2-demo history                                         # all accounts, [history] days (default 30)
psd2-demo history --days 7 --account main                 # one account, last week
psd2-demo history --from 2026-10-01 --to 2026-10-31       # a calendar month
psd2-demo history --include balances,details --output exports/history.json
```

| Option | Meaning |
|---|---|
| `--account NAME` | only this account; repeatable. Default: all, in config order |
| `--days N` / `--from D [--to D]` | the period; `--to` defaults to today. Default: `[history] days` |
| `--include PARTS` | extra parts: `balances`, `details` (account resource), `raw` (each transaction's original API object); `none` = transactions only. Replaces `[history] include` |
| `--output FILE` | write the JSON there (atomic, mode 0600) instead of stdout. A path outside `exports/` gets a warning, because it isn't git-ignored |
| `--max-age MIN` | use cached results up to MIN minutes old (default `[history] max_age`, 60) |
| `--no-cache` | always call the API; neither read nor write the cache |
| `--verbose` | per-account progress and HTTP debug lines (redacted) on stderr |

`history` never prompts, never opens a browser, never starts a consent and never changes stored sessions. Accounts are fetched one after another, and a failing account doesn't stop the others.

**Exit codes:** `0` every account `ok`, `3` partial, `1` no account `ok`, `2` config or usage error (message on stderr, no document), `130` interrupted. For 0, 1 and 3 a complete document is always written. stdout carries only the JSON.

## 3. The JSON contract
Schema: [`schemas/account-history.v1.json`](../schemas/account-history.v1.json) (JSON Schema 2020-12, strict: no extra keys). Example: [`schemas/account-history.v1.example.json`](../schemas/account-history.v1.example.json).

```jsonc
{
  "schema_version": "1",
  "generated_at": "2026-11-08T12:00:00Z",
  "tool": {"name": "psd2-demo", "version": "0.1.0"},
  "range": {"date_from": "2026-10-09", "date_to": "2026-11-08"},
  "include": ["balances"],
  "accounts": [{
    "name": "main", "bank": "KBC", "country": "BE", "iban": "BE68539007547034", "currency": "EUR",
    "status": "ok",                 // ok | consent_required | rate_limited | error
    "error": null,                  // or {step, message, http_status, api_error, hint}
    "warnings": [],                 // e.g. a failed optional part, truncated paging
    "session_valid_until": "2027-05-07T10:00:00+00:00",
    "fetched_at": "2026-11-08T12:00:00Z", "truncated": false,
    "cached": false, "stale": false, "api_calls": 3,
    "transactions": [{
      "id": "ref:REF-001", "booking_date": "2026-11-05", "value_date": "2026-11-05", "transaction_date": null,
      "amount": "-42.50", "currency": "EUR", "counterparty_name": "Delhaize",
      "counterparty_iban": "BE71096123456769", "remittance": "Groceries / Card 1234",
      "status": "BOOK", "reference": "REF-001", "out_of_range": false
    }],
    "balances": [{"name": "Booked balance", "type": "CLBD", "type_label": "closing booked",
                  "amount": "+1234.56", "currency": "EUR", "reference_date": "2026-11-07",
                  "last_change": "2026-11-07T23:59:59Z"}]
  }],
  "summary": {"accounts": {"ok": 1, "consent_required": 0, "rate_limited": 0, "error": 0},
              "totals": {"EUR": {"count": 1, "credits": "+0", "debits": "-42.50", "net": "-42.50"}}}
}
```

Rules applications can rely on:
- **Amounts** are signed decimal strings (`"-42.50"` out, `"+2500.00"` in), never floats. Parse them with a decimal type.
- **Dates and times:** dates are `YYYY-MM-DD`, and timestamps are UTC `…Z` (except `session_valid_until`, which is passed through as the bank sent it).
- **Missing values** are `null`; keys are never dropped. `balances`/`details` exist only when included, and `raw` only with `--include raw`.
- **Order:** transactions are newest first. Rows the bank returned outside the range are kept with `out_of_range: true`.
- **`summary.totals`** covers `ok` accounts only (cached ones included).
- **Versioning:** a breaking change bumps `schema_version` to `"2"` and adds a new schema file.

### Stable transaction ids
`id` is `ref:<bank reference>` when the bank gives one (`entry_reference`, else `transaction_id`). Otherwise it's `h:` plus a hash of the account, date, amount, currency, counterparty and remittance. Repeated fetches yield the same id, so you can de-duplicate across runs.

**Limitation:** two identical transactions (same day, amount, counterparty and text, e.g. two €3.50 coffees) are told apart only by their order in the bank's response. Their hash ids are stable only as long as the bank returns them in the same order.

## 4. Caching and the 4-per-day limit
Without you present, PSD2 lets a bank cap account access at 4 requests per account per day. One account fetch costs:
- `GET /sessions/{id}`, which goes to Enable Banking, not the bank
- one request per transaction page
- one more each for balances and details

So:
- **The cache.** Each complete account result (one without warnings) is cached per (account, consent, period, parts) under `.psd2demo/cache/` (git-ignored, files 0600) and reused for `max_age` minutes. A run where everything is cached makes no network call at all, and shows `cached: true` and `api_calls: 0`. A new consent (`authorize`) starts a fresh cache. A revoked or expired consent is reported as `consent_required` before the cache is consulted.
- **Rate limiting (429).** A 429 on the session check or the transactions falls back to the account's cached result of any age, marked `cached: true, stale: true` with a warning; without a cache, the account is `rate_limited`. A 429 on balances or details keeps the transactions: that part is `null`, with a warning. `history` doesn't retry 429s, because a daily cap won't clear within a run.
- **Shared consents.** Accounts at the same bank share one consent, which is checked once per run.
- **Budgeting.** `api_calls` per account tells you what each run cost.
- **Pruning.** `psd2-demo sessions prune` also deletes cache files older than 7 days.
- **PSU headers.** If the bank's required PSU headers are configured (`psu_ip_address`), requests count as "you are present". See the PSU headers section of the setup guide.

## 5. Scheduling
cron (daily at 07:00, plus a weekly consent check on Monday):
```cron
0 7 * * *  cd /path/to/be-enablebanking-demo && .venv/bin/psd2-demo history --output exports/history.json; [ $? -le 3 ] || echo "history failed" | mail -s psd2 you@example.com
0 9 * * 1  cd /path/to/be-enablebanking-demo && .venv/bin/psd2-demo authorize --status --json > exports/consents.json || echo "renew consents: psd2-demo authorize" | mail -s psd2 you@example.com
```

systemd user timer:
```ini
# ~/.config/systemd/user/psd2-history.service
[Service]
Type=oneshot
WorkingDirectory=%h/dev/be-enablebanking-demo
ExecStart=%h/dev/be-enablebanking-demo/.venv/bin/psd2-demo history --output exports/history.json
SuccessExitStatus=3

# ~/.config/systemd/user/psd2-history.timer
[Timer]
OnCalendar=*-*-* 07:00
Persistent=true
[Install]
WantedBy=timers.target
```
`systemctl --user enable --now psd2-history.timer`

## 6. Python API
```python
from decimal import Decimal

from psd2demo.history import fetch_history

doc = fetch_history(days=30, include=["balances"])          # same document as the CLI
for account in doc["accounts"]:
    if account["status"] == "ok":
        spent = sum(Decimal(t["amount"]) for t in account["transactions"] if t["amount"].startswith("-"))
    elif account["status"] == "consent_required":
        print(account["error"]["hint"])                      # psd2-demo authorize --account <name>
```
- **Arguments:** `config_path`, `accounts`, `days` / `date_from` / `date_to`, `include` (list or comma-separated string), `max_age`, `use_cache`.
- **Behavior:** it reads `./config.toml` by default and keeps `.psd2demo/` relative to the working directory. It prints nothing.
- **Errors:** it raises only `psd2demo.errors.ConfigError` (bad config, include, range or account name) and `KeyboardInterrupt`; everything per account is reported inside the document.

## 7. Claude Code skill
`.claude/skills/bank-account-history/SKILL.md` is part of the repository, so Claude Code picks it up when started from the project directory. Ask in plain language, e.g. "what did I spend in the last 7 days?" or "balances of my accounts".

The skill:
- runs `psd2-demo history`, writes to `exports/history.json`, and answers from that file
- uses the cache, and doesn't add `--no-cache`
- tells you to run `psd2-demo authorize --account <name>` yourself when a consent is missing; it never runs the interactive login itself
- doesn't print full IBANs or whole transaction lists unless asked, and never sends the data elsewhere

Its `allowed-tools` pre-approves only `psd2-demo history` and reading files.

## Troubleshooting
| Symptom | Fix |
|---|---|
| Account `consent_required` | `psd2-demo authorize --account <name>` (interactive) |
| `authorize` reports `missing` | Select the account on the bank's consent screen, or link it in the Enable Banking control panel; then `psd2-demo authorize --account <name> --fresh` |
| `rate_limited`, or `stale: true` | You hit the bank's limit; rely on the cache, set `psu_ip_address`, and fetch less often |
| `error` with step `TRANSACTIONS` and the 90-day hint | Shorten the period, or renew the consent (`authorize --fresh`) |
| Exit 2 | Read the stderr message (unknown account, bad `--include`, bad period, invalid config) |
