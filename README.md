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
psd2-demo run --fresh                 # force a new consent
psd2-demo run --export exports/run.json
psd2-demo run --json                  # machine-readable result
```
Exit code: `0` pass, `1` a step failed, `2` configuration error, `130` interrupted.

## Test
```bash
pytest                                            # all tests (offline)
pytest tests/test_client.py::test_pagination      # a single test
```
No test calls the real API. The production run is manual.

## Docs
- [documentation/index.md](documentation/index.md)
- Specs: [specs/index.md](specs/index.md)
