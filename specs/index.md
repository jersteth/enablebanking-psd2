# Specs Index

## Steering Documents
- [product.md](./product.md) - Business-level overview of the application

## Feature Specs

| # | Slug | Description | Status |
|---|------|-------------|--------|
| 001 | account-access-flow | End-to-end PSD2 AIS flow via Enable Banking: auth, bank discovery, consent, sessions, accounts/balances/transactions | Superseded by 003 |
| 002 | production-transactions-demo | Single-command production E2E demo: one bank + IBAN, consent, last 10 days of transactions, plus Enable Banking setup guide | Implemented, verified in production |
| 003 | accounts-and-sessions | Remaining 001 scope: banks list, balances, account details, date ranges, multiple accounts/sessions, revoke | Implemented, verified in production |
| 004 | account-history-skill | Non-interactive history fetch for all accounts (days, detail levels) to versioned JSON: CLI, Python API, Claude Code skill; authorize-once, caching | Planned (after 003) |
