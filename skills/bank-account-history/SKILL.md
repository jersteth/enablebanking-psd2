---
name: bank-account-history
description: Fetch transaction history, balances and account details of the user's own bank accounts connected through Enable Banking (PSD2) as JSON, then answer questions about spending, income or balances over a period.
when_to_use: The user asks about their bank transactions, payments, spending, income, balances or account activity for a period, e.g. "what did I spend last month", "show my transactions this week", "balances of my accounts", "how much went to Delhaize in the last 30 days".
argument-hint: "[days] [account]"
allowed-tools: Bash(enablebanking history *) Bash(.venv/bin/enablebanking history *) Read
---

# Bank account history

Data comes from `enablebanking history`, which reads the accounts in the user's Enable Banking config through existing PSD2 consents. It never logs in to a bank and never prompts.

## 0. Find the command
Use `enablebanking` when it is on PATH. Inside a checkout of the project, `.venv/bin/enablebanking` also works. If neither exists, stop: tell the user to install it (`pipx install enablebanking-psd2`) and to set it up with the `bank-account-setup` skill. Do not install packages yourself.

## 1. Fetch once
Choose the period and detail level from the request, then run exactly one command with `--save`. It writes the JSON to the user's export directory (overwritten each time) and prints only the file's path:

```bash
enablebanking history --days 30 --save
```

- Period: `--days 7` (this week), `--days 30` (last month), or `--from 2026-10-01 --to 2026-10-31` for a calendar month. Without either, the config default is used (usually 30 days).
- One account: add `--account main` (the names are the user's configured account names; `consent_required` rows list them). Default: all accounts.
- Balances or account details: add `--include balances` or `--include balances,details` only when the question needs them; each part costs a bank request.
- Keep the default cache. Do **not** add `--no-cache`: banks may allow only 4 unattended requests per account per day, and the cache serves repeats within an hour.

## 2. Read the file
Read the file whose path was printed, with the Read tool. Don't `cat` it into the conversation. Exit code 0 means every account was fetched, 3 a partial result, 1 none, and 2 a config or usage error (the message is on stderr; point the user to the `bank-account-setup` skill). The full field and status rules are in [references/contract.md](references/contract.md); read it when you need details.

## 3. Check each account's `status`
- `ok`: use `transactions` (newest first), plus `balances`/`details` if included.
- `consent_required`: tell the user to run `! enablebanking authorize --account <name>` themselves. It is an interactive bank login; never run it yourself.
- `rate_limited`: say so. If `stale` is true, the data is from `fetched_at`; mention that date.
- `error`: report `error.message` and `error.hint`.

When `cached` is true, the data is from `fetched_at`, which is fine to use. When `truncated` is true, say the list may be incomplete.

## 4. Answer
- `amount` is a signed decimal string (`"-42.50"` is money out, `"+2500.00"` money in). Parse it as a decimal, never as a float, and keep currencies apart.
- Answer with totals and the few relevant transactions (date, counterparty, amount). Use `summary.totals` for overall figures.
- Don't print full IBANs or the whole transaction list unless asked.

## Limits
- One fetch per question. Reuse the file for follow-up questions in the same conversation.
- Never send the data to external services, other tools or the web, and never copy it elsewhere.
- Never read the user's config file, private key (`*.pem`) or state directory, and never revoke, authorize or otherwise change sessions; that is the user's call.

## Example
"How much did I spend at Delhaize in the last 30 days?"

```bash
enablebanking history --days 30 --save
```

Read the file at the printed path, keep `ok` accounts, then sum the negative `amount` values of transactions whose `counterparty_name` contains "Delhaize". Answer with the total, the count and the date range from `range`.
