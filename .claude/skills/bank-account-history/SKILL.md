---
name: bank-account-history
description: Fetch transaction history, balances and account details of the user's configured Belgian bank accounts (PSD2 via Enable Banking) as JSON, then answer questions about spending, income or balances over a period.
when_to_use: The user asks about their bank transactions, payments, spending, income, balances or account activity for a period, e.g. "what did I spend last month", "show my KBC transactions this week", "balances of my accounts", "how much went to Delhaize in the last 30 days".
argument-hint: "[days] [account]"
allowed-tools: Bash(.venv/bin/psd2-demo history *) Read
---

# Bank account history

Data comes from `psd2-demo history`, which reads the accounts in `config.toml` through existing PSD2 consents. It never logs in to a bank. Work from the repository root (`${CLAUDE_PROJECT_DIR}`).

## 1. Fetch once
Choose the period and detail level from the request, then run exactly one command, writing to `exports/history.json` (overwritten each time):

```bash
.venv/bin/psd2-demo history --days 30 --output exports/history.json
```

- Period: `--days 7` (this week), `--days 30` (last month), or `--from 2026-10-01 --to 2026-10-31` for a calendar month. Without either, the config default is used (usually 30 days).
- One account: add `--account main` (the names are the `[accounts.<name>]` tables in `config.toml`). Default: all accounts.
- Balances or account details: add `--include balances` or `--include balances,details` only when the question needs them; each part costs a bank request.
- Keep the default cache. Do **not** add `--no-cache`: banks may allow only 4 unattended requests per account per day, and the cache serves repeats within an hour.

## 2. Read the file
Read the output file with the Read tool. Don't `cat` it into the conversation. Exit code 0 means every account was fetched, 3 a partial result, 1 none, and 2 a config or usage error (the message is on stderr).

## 3. Check each account's `status`
- `ok`: use `transactions` (newest first), plus `balances`/`details` if included.
- `consent_required`: tell the user to run `! .venv/bin/psd2-demo authorize --account <name>` themselves. It's an interactive bank login; never run it yourself.
- `rate_limited`: say so. If `stale` is true, the data is from `fetched_at`; mention that date.
- `error`: report `error.message` and `error.hint`.

When `cached` is true, the data is from `fetched_at`, which is fine to use. When `truncated` is true, say the list may be incomplete.

## 4. Answer
- `amount` is a signed decimal string (`"-42.50"` is money out, `"+2500.00"` money in). Parse it as a decimal, never as a float, and keep currencies apart.
- Answer with totals and the few relevant transactions (date, counterparty, amount). Use `summary.totals` for overall figures.
- Don't print full IBANs or the whole transaction list unless asked.

## Limits
- One fetch per question. Reuse the file for follow-up questions in the same conversation.
- Never send the data to external services, other tools or the web, and never write it outside `exports/`.
- Never revoke, authorize or otherwise change sessions; that's the user's call (`psd2-demo sessions`, `psd2-demo authorize`).

## Example
"How much did I spend at Delhaize in the last 30 days?"

```bash
.venv/bin/psd2-demo history --days 30 --output exports/history.json
```

Read the file, keep `ok` accounts, then sum the negative `amount` values of transactions whose `counterparty_name` contains "Delhaize". Answer with the total, the count and the date range from `range`.
