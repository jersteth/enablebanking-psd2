# Specs Index

## Steering Documents
- [product.md](./product.md) - Business-level overview of the application

## Feature Specs

| # | Slug | Description | Status |
|---|------|-------------|--------|
| 001 | account-access-flow | End-to-end PSD2 AIS flow via Enable Banking: auth, bank discovery, consent, sessions, accounts/balances/transactions | Superseded by 003 |
| 002 | production-transactions-demo | Single-command production E2E demo: one bank + IBAN, consent, last 10 days of transactions, plus Enable Banking setup guide | Implemented, verified in production |
| 003 | accounts-and-sessions | Remaining 001 scope: banks list, balances, account details, date ranges, multiple accounts/sessions, revoke | Implemented, verified in production |
| 004 | account-history-skill | Non-interactive history fetch for all accounts (days, detail levels) to versioned JSON: CLI, Python API, Claude Code skill; authorize-once, caching | Implemented, verified in production |
| 005 | agent-skill-package | `enablebanking-psd2`: reusable by other developers' agents (bring your own Enable Banking app, any country): PyPI package, location-independent config/state, `init` onboarding, portable skills + Claude Code plugin, read-only MCP server, examples, release | Implemented (pending verification) |
