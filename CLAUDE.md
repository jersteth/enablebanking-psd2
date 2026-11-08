# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project
`be-enablebanking-demo`: a test client for PSD2 account access (AIS) to Belgian personal bank accounts, using the Enable Banking API (enablebanking.com) as the aggregator. See `./specs/product.md`.

## Commands
```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # setup (no uv on this machine)
.venv/bin/pytest                                              # all tests (offline, respx-mocked HTTP)
.venv/bin/pytest tests/test_client.py::test_pagination        # single test
.venv/bin/psd2-demo check | run [--fresh] [--export F] [--json] [--verbose]
```
Config: `config.toml` (git-ignored; template `config.example.toml`). Precedence: CLI flag > `EB_*` env > TOML > defaults.

## Architecture
- `src/psd2demo/eb/`: reusable Enable Banking client with no CLI/IO. `auth.py` (RS256 JWT, `kid`=app id, cached), `client.py` (httpx; GET retries on 429/5xx/network, POST only on 429 because the `/sessions` code is single-use; `iter_transactions` follows `continuation_key` with a loop guard), `models.py`, `errors.py` (`ApiError` hierarchy with hints).
- `flow.py` orchestrates the steps CONFIG → BANK → CONSENT → ACCOUNT → TRANSACTIONS. Each step's `DemoError`/`ApiError` is reported by `StepReporter` and mapped to an exit code. Side effects (capture, browser, store, today/now) are injectable for tests.
- `consent.py`: `state`, consent validity, redirect capture (`paste` by default; `listener` = one-shot 127.0.0.1 server, only for `http://localhost:<port>` redirect URLs), callback parsing.
- `session_store.py`: `.psd2demo/session.json` (0600, atomic). Stores an IBAN hash, never the IBAN.
- `cli.py` builds config → signer → client, runs the flow, and renders (`output.py`) or prints `--json`.
- Never log the JWT, the authorization code/state, or non-target IBANs. `log.RedactingFilter` enforces this.
- Testing pitfall: resolve `sys.stderr`/`input` at call time, not as default arguments, so pytest capture and monkeypatch work.

## Development Process

### Steering Documents
The `./specs` folder contains steering documents for the project:
- `./specs/product.md` - Business-level overview of the application
- `./specs/index.md` - Index of all feature specs

### Spec-Driven Development
For each new major feature, create a new folder in `./specs` with sequential numbering (e.g., `001-feature-name`) containing:
- `requirements.md` - Detailed description of the feature requirements
- `design.md` - Detailed design of the feature
- `tasks.md` - List of tasks, grouped by phases, to implement the feature

### Documentation
When a user request is completed, create or update documentation in `./documentation` to reflect the changes:
1. Keep one file per functional or technical domain area
2. **Always update `./documentation/index.md`** when adding new documentation files
3. Before finishing any task that adds or modifies features, check if documentation needs updating
4. Reference `./documentation/index.md` to see existing documentation topics
