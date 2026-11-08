# Agent Integration

How developers let an agent read **their own** bank accounts through Enable Banking. Each user brings their own Enable Banking application ([setup guide](./enablebanking-setup.md)); nothing is shared or hosted. The data stays on the user's machine, apart from whatever the agent itself reads (see Privacy).

## Choose an entry point
| You build | Use | Install |
|---|---|---|
| a Claude Code workflow | the **plugin** with the `bank-account-history` and `bank-account-setup` skills | `claude plugin marketplace add jersteth/enablebanking-psd2` then `claude plugin install enablebanking-psd2@enablebanking-psd2`, plus `pipx install enablebanking-psd2` |
| a Claude Agent SDK agent | the same skills through the SDK `plugins` option (`examples/agent_sdk_agent.py`) | `pip install claude-agent-sdk enablebanking-psd2` |
| an agent in any other framework (or the Claude API with MCP) | the **MCP server** `enablebanking mcp` | `uvx --from 'enablebanking-psd2[mcp]' enablebanking mcp`; config snippet in `examples/mcp.json` |
| Python code | `from enablebanking_psd2 import fetch_history, consent_status` (`examples/fetch_history.py`) | `pip install enablebanking-psd2` |
| a scheduler or any language | the CLI `enablebanking history` (JSON on stdout or `--save`) | `pipx install enablebanking-psd2` |

All of them return the same versioned JSON document ([account-history.md](./account-history.md#3-the-json-contract), schema v1). The Claude API's skill upload is not supported: its sandbox cannot reach Enable Banking or use your key.

## One-time user setup
1. Register an Enable Banking application and link your accounts ([setup guide](./enablebanking-setup.md)).
2. `enablebanking init` writes the config.
3. `enablebanking authorize` logs in at each bank once (about 180 days). An agent never does this step: it needs you at the bank. In Claude Code, type `! enablebanking authorize`.
4. Ask the agent. The `bank-account-setup` skill walks through steps 1-3 and checks them with `enablebanking authorize --status`.

## What the agent can do
- **Skill `bank-account-history`:** runs `enablebanking history ... --save`, reads the JSON file at the path it prints, and answers. `allowed-tools` pre-approves only `enablebanking history` and `Read`.
- **Skill `bank-account-setup`:** guidance, and the read-only commands `--version`, `check` and `authorize --status`.
- **MCP tools:** `get_history(accounts?, days?, date_from?, date_to?, include?)` and `consent_status()`, both read-only. No tool starts a consent, revokes a session or writes a file.

## Rules for agent builders
- **Never give an agent the private key, the config file or the state folder.** The skills tell the model not to read them; for your own agents, scope file access so they cannot. The key is the credential for your Enable Banking application.
- **Consent is a human step.** Do not automate `authorize`; it needs your login and strong authentication at the bank.
- **Pre-approve narrowly.** Allow `enablebanking history *` (and `Read`), not a general shell.
- **Respect the rate limits.** Banks may allow only 4 unattended requests per account per day. Results are cached (default 60 minutes); do not add `--no-cache` or call in loops. `api_calls`, `cached` and `stale` in the document show what a call cost. Setting `psu_ip_address` marks requests as "user present".
- **Handle statuses.** `consent_required` means ask the user to run `authorize`; `rate_limited` and `stale: true` mean use the cache and wait.
- **Parse amounts as decimals**, never floats; they are signed strings.

## Privacy
The JSON holds personal financial data (counterparties, descriptions, balances, IBANs). It is written with file mode 0600 into a 0700 folder. Whatever an agent reads is sent to its model provider: request `--include balances`/`details` only when needed, and have the agent summarise instead of printing transaction lists or full IBANs.

## Platform notes
Linux and macOS are supported. On Windows the tool works, but the 0600/0700 modes are not enforced and config, state and exports live under `%APPDATA%\enablebanking-psd2`.
