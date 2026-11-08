# be-enablebanking-demo

End-to-end PSD2 demo: read the last 10 days of transactions of **one Belgian personal account** through the [Enable Banking](https://enablebanking.com) API, in production restricted mode (your own linked accounts, no contract needed).

## Prerequisites
- Python ≥ 3.11
- An Enable Banking production application with your account linked: follow **[documentation/enablebanking-setup.md](documentation/enablebanking-setup.md)**

## Install
```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cp config.example.toml config.toml   # then edit it
```

## Use
```bash
psd2-demo check                       # verify config, key and API access (no consent)
psd2-demo run                         # consent at your bank, then the last 10 days of transactions
psd2-demo transactions --days 30      # another period (or --from 2026-10-01 --to 2026-10-15)
psd2-demo balances                    # balances
psd2-demo accounts --details          # accounts in the consent, plus the bank's account data
psd2-demo banks --filter kbc          # bank names, BIC, consent limits, required PSU headers
psd2-demo sessions                    # stored consents; `sessions revoke --account main` closes one
psd2-demo run --account savings       # with several [accounts.<name>] in config.toml
psd2-demo run --fresh --export exports/run.json --json
psd2-demo authorize                   # once per bank, for unattended use (~180-day consent)
psd2-demo history --days 30 --output exports/history.json   # all accounts → versioned JSON (exit 0/3/1)
```
Applications: `psd2demo.history.fetch_history()` returns the same document; schema in `schemas/account-history.v1.json`. In Claude Code, the `bank-account-history` skill answers questions such as "what did I spend last week?". See [documentation/account-history.md](documentation/account-history.md).
Exit code: `0` pass, `1` a step failed, `2` configuration or usage error, `130` interrupted (`history`: `3` = some accounts ok).

## Test
```bash
pytest                                            # all tests (offline)
pytest tests/test_client.py::test_pagination      # a single test
```
No test calls the real API. The production run is manual.

## Docs
- [documentation/index.md](documentation/index.md)
- Specs: [specs/index.md](specs/index.md)
