# Changelog

## 0.2.0
- **Renamed** the project to `enablebanking-psd2`: the import package is `enablebanking_psd2`; commands `enablebanking` (short), `enablebanking-psd2` and `psd2-demo` (alias). `import psd2demo` still works with a `DeprecationWarning` and is removed in 0.3.
- **Works from any directory:** config (`--config`, `ENABLEBANKING_PSD2_CONFIG`, `./config.toml`, `~/.config/enablebanking-psd2/config.toml`), state and exports follow XDG paths when the project-folder locations (`./.psd2demo`, `./exports`) don't exist; `[paths]` section; config-less operation from `EB_*` variables.
- **Any country:** `[enablebanking] country` (default `BE`) for `check` and `banks`; IBAN length checks for every IBAN country.
- `history --save` writes `<export folder>/history.json` and prints its path.
- `enablebanking init`: guided setup.
- Python API: `enablebanking_psd2.fetch_history` and `consent_status`.
- Agent skills `bank-account-history` and `bank-account-setup` (`skills/`), packaged as a Claude Code plugin and marketplace.
- `enablebanking mcp`: read-only MCP server with `get_history` and `consent_status` (extra `[mcp]`).
- The JSON Schema ships inside the package. Schema version stays `1`; its `$id` URL changed.
- Hints now say `enablebanking authorize ...`.

## 0.1.0
Belgian PSD2 demo (specs 002-004): `run`, `transactions`, `balances`, `accounts`, `banks`, `sessions`, `authorize`, `history`, JSON contract v1, cache, Claude Code skill.
