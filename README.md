# enablebanking-psd2

Read **your own** bank accounts through PSD2 with the [Enable Banking](https://enablebanking.com) API: transactions, balances and account details as a stable JSON document, for scripts, Python code and AI agents (Claude Code skills, MCP). It runs on your machine, in Enable Banking's free production *restricted* mode: you register your own application, link your own accounts, and nothing is shared or hosted. Tested with Belgian banks; it works for any country Enable Banking supports.

## Quick start
```bash
pipx install enablebanking-psd2          # or: pip install enablebanking-psd2
enablebanking init                       # guided setup (needs your Enable Banking app: see below)
enablebanking authorize                  # log in at your bank once (about 180 days)
enablebanking history --days 30 --save   # all accounts -> JSON in your export folder (path is printed)
```
Prerequisite: an Enable Banking production application with your accounts linked, see **[documentation/enablebanking-setup.md](documentation/enablebanking-setup.md)**. Python ≥ 3.11.

## For agents
```bash
claude plugin marketplace add jersteth/enablebanking-psd2
claude plugin install enablebanking-psd2@enablebanking-psd2
```
Then ask Claude Code "what did I spend in the last 7 days?" (skill `bank-account-history`; `bank-account-setup` helps with setup). Other frameworks: `uvx --from 'enablebanking-psd2[mcp]' enablebanking mcp` (read-only MCP server), or `from enablebanking_psd2 import fetch_history`. See **[documentation/agent-integration.md](documentation/agent-integration.md)** and `examples/`.

## Commands
```bash
enablebanking check                       # verify config, key and API access (no consent)
enablebanking run                         # consent at your bank, then the last 10 days of transactions
enablebanking transactions --days 30      # another period (or --from 2026-10-01 --to 2026-10-15)
enablebanking balances                    # balances
enablebanking accounts --details          # accounts in the consent, plus the bank's account data
enablebanking banks --country NL --filter ing   # bank names, BIC, consent limits, required PSU headers
enablebanking sessions                    # stored consents; `sessions revoke --account main` closes one
enablebanking authorize [--status]        # once per bank, for unattended use (~180-day consent)
enablebanking history --days 30 --save    # all accounts -> versioned JSON (exit 0/3/1)
enablebanking init | mcp                  # guided setup | MCP server
```
`psd2-demo` still works as an alias. Exit code: `0` pass, `1` a step failed, `2` configuration or usage error, `130` interrupted (`history`: `3` = some accounts ok). The JSON contract is described in [documentation/account-history.md](documentation/account-history.md); the schema ships in the package (`enablebanking_psd2/schemas/account-history.v1.json`).

## Develop
```bash
git clone https://github.com/jersteth/enablebanking-psd2 && cd enablebanking-psd2
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest                                            # all tests (offline; no test calls the real API)
```
The production run is manual. Specs: [specs/index.md](specs/index.md); docs: [documentation/index.md](documentation/index.md); changes: [CHANGELOG.md](CHANGELOG.md). License: Apache-2.0.
