# 005 – Agent Skill Package: Requirements

## Introduction
Makes the 004 capability reusable by **other developers** who build agents that read **their own** personal accounts, in any country Enable Banking covers, through **their own** Enable Banking application. Today everything only works inside this repo: the venv path in the skill, `./config.toml`, `./.psd2demo/`, and the "demo" naming. 005 packages the existing engine and gives it three thin entry points:
1. a Python package on PyPI, providing the CLI and the Python API
2. portable Agent Skills, distributed as a Claude Code plugin and as plain skill folders
3. a read-only MCP server

No new bank logic is added.

## Alignment with Product Vision
`specs/product.md` targets PSD2 access to personal Belgian accounts via Enable Banking. 005 extends the audience from the owner to any developer acting as owner of their own accounts, and the geography from Belgium to every Enable Banking country (hence the name `enablebanking-psd2`; Belgium stays the default and the tested case). The scope otherwise stays the same: personal accounts, AIS only, production restricted mode. `product.md` is updated to match. Each user brings their own app; the project runs no shared app, hosts nothing, and never receives anyone's bank data.

## Dependencies
- **004** (implemented): `history`, `authorize`, `fetch_history()`, schema v1, the cache and the skill.
- **003**: the multi-account config and the session store v2.

## Requirements

### R1 – Installable Package
**As** a developer, **I want** to install the tool with one command, **so that** I don't have to clone the repo.
- AC1: The distribution `enablebanking-psd2` installs with `pipx install enablebanking-psd2` and runs with `uvx enablebanking-psd2`. Its console scripts are `enablebanking-psd2` and the short `enablebanking` (identical; docs and skills use `enablebanking`). `psd2-demo` stays as a backward-compatible alias.
- AC2: The import package is `enablebanking_psd2`. `import psd2demo` keeps working for one minor release, with a `DeprecationWarning`.
- AC3: `pyproject.toml` `name`, the schema `$id` and the project URLs use `enablebanking-psd2` / `https://github.com/jersteth/enablebanking-psd2`. The package metadata has a license, a project URL, a Python version range and classifiers. The schema file and its example ship inside the package, readable through `importlib.resources`.

### R2 – Location Independence
**As** a developer, **I want** the tool to work from any directory, **so that** agents can call it wherever they run.
- AC1: Config resolution: `--config` > `$ENABLEBANKING_PSD2_CONFIG` > `./config.toml` (backward compatible) > `$XDG_CONFIG_HOME/enablebanking-psd2/config.toml` (default `~/.config/enablebanking-psd2/config.toml`).
- AC2: State (sessions, cache): `[paths] state_dir` > `$ENABLEBANKING_PSD2_STATE_DIR` > `./.psd2demo` if it exists (backward compatible) > `$XDG_STATE_HOME/enablebanking-psd2`. The directory is created 0700.
- AC3: The default export directory is `$XDG_DATA_HOME/enablebanking-psd2/exports`. Writing outside it gives the 004 notice only inside a git work tree.
- AC4: Running with no config file, from `EB_*` environment variables alone, works for `history` and `authorize --status`.
- AC5: The existing repo layout and all existing tests keep working unchanged.

### R3 – Onboarding
**As** a developer new to Enable Banking, **I want** guided setup, **so that** I reach a first successful fetch quickly.
- AC1: `enablebanking init` is interactive:
  - asks for the app ID, the key path and the redirect URL
  - rejects a key file readable by group or others, with a `chmod 600` hint
  - asks for the country (default BE), searches that country's banks (reusing `banks --country --filter`) and adds one or more accounts
  - writes the config (mode 0600, never overwriting without confirmation), then runs `check`
- AC2: `init` never prints the key contents or full IBANs other than the ones the user typed.
- AC3: `documentation/enablebanking-setup.md` becomes a "bring your own Enable Banking app" guide, for any user: register the app, restricted production, link your own accounts, the redirect URL, PSU IP, consent length.

### R4 – Portable Agent Skills
**As** an agent developer, **I want** skills that work outside this repo, **so that** my agent can fetch bank data through the documented contract.
- AC1: The skills live in `skills/` at the repo root in the Agent Skills format (a `SKILL.md` per folder, optional `references/`). They work in Claude Code, Agent SDK filesystem skills and Claude API skill upload.
- AC2: `bank-account-history`:
  - calls `enablebanking history` on PATH, with `allowed-tools: Bash(enablebanking history *) Read`
  - writes to the default export directory
  - when the CLI is missing, tells the user to install it
  - keeps the 004 rules: never runs `authorize`, keeps the cache, no exfiltration, masked IBANs
- AC3: `references/contract.md` condenses the schema v1 rules (amounts, ids, statuses, exit codes), so an agent can interpret the output without the repo.
- AC4: `bank-account-setup` guides installation, `init` and `authorize`, and tells the user to run the interactive commands themselves (in Claude Code: `! enablebanking authorize`). It never reads the config or the key file.
- AC5: The repo (`jersteth/enablebanking-psd2`, renamed from `be-enablebanking-demo`; GitHub redirects the old URL) is a Claude Code plugin marketplace (`.claude-plugin/marketplace.json` plus `plugin.json`), installable with `/plugin marketplace add jersteth/enablebanking-psd2` and `/plugin install`.
- AC6: The project-local skill in this repo keeps working, without a second copy.
- AC7: A test parses every skill's frontmatter and validates every CLI command line in the skill bodies against the argument parser.

### R5 – Read-Only MCP Server
**As** a developer using a non-Claude agent framework, **I want** an MCP server, **so that** my agent can use the data without shelling out.
- AC1: `enablebanking mcp` runs the server over stdio. The `mcp` SDK is an install extra (`pip install 'enablebanking-psd2[mcp]'`, `uvx --from 'enablebanking-psd2[mcp]' enablebanking-psd2 mcp`), so the core stays light. Without it, the command exits 2 with an install hint.
- AC2: It has two tools:
  - `get_history(accounts?, days?, date_from?, date_to?, include?)` returns the schema-v1 document, via `fetch_history()`
  - `consent_status()` returns the `authorize --status` rows
- AC3: There is no tool that starts a consent, revokes a session or writes a file. The tool descriptions state the 4-per-day limit and the cache.

### R6 – Examples and Integration Docs
**As** an agent developer, **I want** working examples, **so that** I can copy a known-good integration.
- AC1: `examples/` contains:
  - an Agent SDK agent that loads `skills/bank-account-history`
  - a plain Python script using `fetch_history()`
  - an MCP client configuration snippet
- AC2: `documentation/agent-integration.md` covers:
  - the three entry points and when to use each
  - security rules: never give an agent the key or config, data stays local, consent is a human step, `allowed-tools` scoping
  - rate limits and caching
- AC3: The README has a developer quick start: install, `init`, `authorize`, then ask the agent.

### R7 – Release
**As** the maintainer, **I want** reproducible releases, **so that** users get tested builds.
- AC1: A GitHub Actions workflow runs the tests on each push and PR.
- AC2: A tag `v*` builds the sdist and wheel and publishes them to PyPI with trusted publishing (no stored token).
- AC3: Version 0.2.0. A `CHANGELOG.md` records the rename and the new paths.

### R8 – Any Enable Banking Country
**As** a developer outside Belgium, **I want** the tool to work with my country's banks, **so that** the package name holds.
- AC1: Nothing is limited to BE. `bank_country` (per account) and `banks --country` default to `BE` for backward compatibility; the `init` wizard asks for the country.
- AC2: `check` without an account uses a configurable country (`[enablebanking] country`, default `BE`) instead of the hard-coded `BE`, and its message drops "Belgian".
- AC3: IBAN validation knows the official length of every IBAN country (not only BE), keeping the mod-97 check.
- AC4: The CLI and package descriptions, README and docs say "personal accounts via Enable Banking", with Belgium as the tested example. Amount and currency handling already supports non-EUR currencies (schema `totals` per currency); tests add one non-BE, non-EUR account.

## Non-Functional Requirements
- **Compatibility:** schema v1 and the exit codes are unchanged, and existing configs and state keep working.
- **Security:**
  - no shared credentials, telemetry or network calls other than Enable Banking
  - config, key, state and exports stay 0600/0700
  - the skills and the MCP server are read-only, and never show the key, JWTs or authorization codes
- **Privacy:** documentation states that the data is personal, stays on the user's machine, and that the agent's model provider sees whatever the agent reads.
- **Portability:**
  - Python ≥ 3.11, Linux and macOS; Windows best effort (no 0600 guarantee, documented)
  - no repo-relative paths in shipped skills
- **Testing:** offline (respx), including config and state resolution with a temporary `HOME`/XDG, scripted `init` stdin, skill command validation, and the MCP tools called in process.
- **Usability:** from a clean machine to a first `history` result needs only install, `init` and `authorize`, plus the Enable Banking registration.
