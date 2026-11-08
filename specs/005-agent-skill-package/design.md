# 005 – Agent Skill Package: Design

Builds on 004 as implemented: `history.fetch_history`, `commands.history`/`authorize`, `contract.py`, `cache.py`, `bootstrap.py`, `fileio.write_text_atomic`, `tests/test_skill.py`. No bank or contract logic changes, and schema v1 stays byte-compatible.

## Overview
- **Rename.** The import package becomes `enablebanking_psd2` (`git mv src/psd2demo src/enablebanking_psd2`) and the distribution `enablebanking-psd2`. There are three console scripts, `enablebanking` (documented), `enablebanking-psd2` and `psd2-demo`, all pointing to `enablebanking_psd2.cli:main`. `psd2demo` becomes a deprecated alias package.
- **`paths.py`** resolves the config file, state directory and export directory: an explicit value, then an environment variable, then the legacy `./` location if it exists, then the XDG default. `load_config` uses it, so the CLI, the Python API and MCP behave the same from any directory.
- **Public API `api.py`:** `fetch_history` (moved from `history.py`, re-exported there) and the new `consent_status`. The MCP server and the examples use only this module.
- **`onboarding.py`** implements `enablebanking init`.
- **`skills/`** at the repo root holds the portable skills. The repo root is also a Claude Code plugin (`.claude-plugin/plugin.json`) and its own marketplace (`.claude-plugin/marketplace.json`, `source: "./"`). `.claude/skills/bank-account-history` becomes a symlink into `skills/`.
- **`mcp_server.py`** is a stdio MCP server with 2 read-only tools wrapping `api.py`. It needs the `[mcp]` install extra.
- **Country:** a `[enablebanking] country` setting (default `BE`), and IBAN lengths for every country in the IBAN registry.
- **CI and release:** GitHub Actions, PyPI trusted publishing, version 0.2.0.

## Architecture
```
            ┌─────────────── entry points ────────────────────────────────┐
 Claude Code / Agent SDK        any MCP client            Python apps
   skills/bank-account-history    enablebanking mcp         from enablebanking_psd2 import fetch_history
   skills/bank-account-setup      (mcp_server.py)
        │ Bash: enablebanking history --save …   │ tools                │
        ▼                                         ▼                      ▼
   cli.py ── commands.history ──────────────► api.fetch_history / api.consent_status
   cli.py ── init ──► onboarding.run ─────► config TOML (XDG) ──► commands.check
                                                  │
                         config.load_config ◄── paths.resolve_* (flag > env > legacy ./ > XDG)
                                                  │
                     history.build_document · flow · eb/ client · cache · session_store (unchanged)
```

Distribution:
```
PyPI enablebanking-psd2 (wheel: enablebanking_psd2/, enablebanking_psd2/schemas/*.json, psd2demo/ shim)
GitHub jersteth/enablebanking-psd2
  .claude-plugin/{plugin.json, marketplace.json}   ← /plugin marketplace add jersteth/enablebanking-psd2
  skills/{bank-account-history, bank-account-setup}/SKILL.md (+ references/)
```

## Components and Interfaces

### Package layout and rename
- `src/psd2demo/` becomes `src/enablebanking_psd2/`. Every internal import is renamed, and the logger name `log.LOGGER = "enablebanking_psd2"`.
- **`src/psd2demo/__init__.py` (shim)** emits `warnings.warn(..., DeprecationWarning, stacklevel=2)`. It installs a `sys.meta_path` finder that maps `psd2demo[.x]` to the same module objects as `enablebanking_psd2[.x]`, so a monkeypatch on either name hits the same object. It is removed in 0.3.
- **`pyproject.toml`:**
  ```toml
  [project]
  name = "enablebanking-psd2"
  dynamic = ["version"]                    # from enablebanking_psd2/__init__.py __version__ = "0.2.0"
  description = "PSD2 account access to your own bank accounts via Enable Banking: CLI, Python API, agent skills, MCP"
  license = "Apache-2.0"                   # the repo LICENSE
  requires-python = ">=3.11"
  dependencies = ["httpx>=0.27", "PyJWT[crypto]>=2.8"]
  classifiers = ["Programming Language :: Python :: 3", "Operating System :: POSIX", "Topic :: Office/Business :: Financial"]
  [project.urls]
  Homepage = "https://github.com/jersteth/enablebanking-psd2"
  [project.optional-dependencies]
  mcp = ["mcp>=1.2"]
  dev = ["pytest>=8", "respx>=0.21", "jsonschema>=4.18", "mcp>=1.2", "build"]
  [project.scripts]
  enablebanking = "enablebanking_psd2.cli:main"
  enablebanking-psd2 = "enablebanking_psd2.cli:main"
  psd2-demo = "enablebanking_psd2.cli:main"
  [tool.hatch.version]
  path = "src/enablebanking_psd2/__init__.py"
  [tool.hatch.build.targets.wheel]
  packages = ["src/enablebanking_psd2", "src/psd2demo"]
  ```
- **The schemas** move to `src/enablebanking_psd2/schemas/` (with `git mv`), so they ship in the wheel and work in an editable install.
  - `contract.schema_path(version="1") -> Traversable` uses `importlib.resources.files(__package__) / "schemas" / ...`.
  - `$id` becomes `https://github.com/jersteth/enablebanking-psd2/schemas/account-history.v1.json`. That's an identifier only: no structural change, so the version stays v1.
  - Doc links are updated. `tests/history_docs.py` writes the example there.
- **`cli.py`:** the `prog` comes from `sys.argv[0]` basename; the description changes to "PSD2 account access via Enable Banking"; new subcommands `init` and `mcp`.

### `paths.py` (new)
```python
APP = "enablebanking-psd2"
def config_file(explicit: Path | None, env: Mapping[str, str], cwd: Path) -> Path | None
    # explicit > env ENABLEBANKING_PSD2_CONFIG > cwd/config.toml if exists > user_config_dir()/config.toml if exists > None
def default_config_target(env) -> Path            # where init writes: env ENABLEBANKING_PSD2_CONFIG or user_config_dir()/config.toml
def state_dir(configured: str | None, env, cwd, base: Path | None) -> Path
    # [paths] state_dir (relative → to `base`, the config file's dir) > env ENABLEBANKING_PSD2_STATE_DIR
    # > cwd/.psd2demo if exists > user_state_dir()
def export_dir(configured, env, cwd, base) -> Path
    # [paths] export_dir > env ENABLEBANKING_PSD2_EXPORT_DIR > cwd/exports if exists > user_data_dir()/exports
def user_config_dir(env) / user_state_dir(env) / user_data_dir(env) -> Path
    # POSIX: $XDG_CONFIG_HOME|~/.config, $XDG_STATE_HOME|~/.local/state, $XDG_DATA_HOME|~/.local/share, + /APP
    # Windows (os.name == "nt"): %APPDATA%/APP for all three (0600/0700 not enforced; documented)
def in_git_worktree(path: Path) -> bool           # any parent (incl. itself) contains .git
```
No new dependency (no `platformdirs`). Directories are created lazily with 0700 by the existing writers (`fileio.write_text_atomic(dir_mode=0o700)`, `SessionStore`, `HistoryCache`).

### `config.py` changes
- `load_config(path, env, overrides, *, cwd: Path | None = None)`. `cwd` defaults to `Path.cwd()`.
  - It resolves the file through `paths.config_file`. A missing explicit path or `ENABLEBANKING_PSD2_CONFIG` stays an error; a missing default isn't (the env-only run, R2 AC4).
- New `Config` fields:
  - `config_path: Path | None`
  - `state_dir: Path` (now resolved, no longer `Path(".psd2demo")`)
  - `export_dir: Path`
  - `country: str`, from `[enablebanking] country` / `EB_COUNTRY`, default `"BE"`, validated as 2 uppercase letters
- New TOML section `[paths]` with `state_dir` and `export_dir`, both optional.
- `NO_ACCOUNTS` doesn't name `config.toml` any more: "no accounts configured; run `enablebanking init` or add [accounts.<name>] tables".
- Existing env names (`EB_*`) stay; new names use the `ENABLEBANKING_PSD2_*` prefix, for paths only.

### `commands.py` changes
- `EXPORTS_DIR` is removed. `_outside_exports_note(path, cfg)` returns `None` when `path` is under `cfg.export_dir` or not inside a git work tree (`paths.in_git_worktree`). Otherwise it returns the 004 note, naming `cfg.export_dir`.
- `check` without an account calls `get_aspsps(cfg.country)`. Message: `credentials ok, N banks in {country} (no account selected)`.
- `banks --country` defaults to `None`, and then `cfg.country` is used.
- `history`:
  - New `--save` option, mutually exclusive with `--output`. It writes to `cfg.export_dir / "history.json"` (atomic, 0600) and prints one line to stdout, the absolute path. With `--output`, stdout stays empty; without either, the JSON goes to stdout.
  - Exit codes are unchanged. `--save` is what the skill uses: a fixed, permission-matchable command, and the path comes back to the agent.
- `_authorize_status` is split:
  - `consent_rows(ctx, targets) -> list[dict]`, pure
  - `ok = all(r["status"] == "authorized" ...)`
  - `api.consent_status` reuses `consent_rows`.

### `api.py` (new, public; `enablebanking_psd2/__init__.py` re-exports both functions)
```python
def fetch_history(config_path=None, accounts=None, days=None, date_from=None, date_to=None, include=None, *,
                  max_age=None, use_cache=True, env=None, client=None, today=None, now=None, progress=None) -> dict
    # body moved verbatim from history.py; history.fetch_history = api.fetch_history (back-compat import)
def consent_status(config_path=None, accounts=None, *, env=None, client=None, now=None) -> dict
    # {"accounts": [{"account","bank","iban_masked","status","valid_until"}...]}; raises only ConfigError;
    # quiet StepReporter; client created lazily (no client when no account has a stored record) and closed
```
`commands.history` keeps calling `fetch_history`. The two functions share a private `_session(cfg, client)` context manager: `load_signer` / `make_client(retry_rate_limits=False)`, then close.

### `onboarding.py` (new): `enablebanking init [--config PATH] [--force]`
```python
@dataclass
class Prompter:                                     # injectable for tests
    ask: Callable[[str], str] = input               # resolved at call time (pytest capture rule)
    out: Callable[[str], None] = print
def run(args, env, prompter: Prompter | None = None, client_factory=None) -> int
```
Flow:
1. **Target:** `args.config` or `paths.default_config_target(env)`. If it exists and `--force` isn't given, ask "overwrite? [y/N]"; no means exit 1, writing nothing.
2. **App ID:** must be a UUID; ask again until valid, or give up after 3 attempts with exit 2.
3. **Key path:** expanded. The file must exist and be readable.
   - If `mode & 0o077`, print the `chmod 600 <path>` hint and stop (exit 2): this is the "rejects" rule (R3 AC1).
   - `bootstrap.load_signer` validates the PEM. The key contents are never printed.
4. **Redirect URL:** defaults to `https://localhost/callback`, validated with the `config.py` rules.
5. **PSU IP:** optional, empty means skipped. Prompt text: "your public IP (`curl -s https://ifconfig.me`), needed by many banks".
6. **Country:** default `BE`.
7. **Account loop:**
   - The user enters a bank search text, and `client.get_aspsps(country)` is filtered like `commands.banks`.
   - Up to 20 numbered matches are shown, and the user picks one.
   - IBAN: normalized and validated with `iban.validate`.
   - Account name: `[a-z0-9_-]+`, unique.
   - "another account? [y/N]".
8. **Write the TOML** with `_render_toml(values) -> str` (strings via `json.dumps` with TOML-compatible escaping; `default_account` before the first table), using `fileio.write_text_atomic(target, dir_mode=0o700)`, mode 0600.
9. **Check:** run `cli.main(["check", "--config", str(target)])` and return its exit code. Then print the next step: `enablebanking authorize`.

The client for step 7 comes from `make_client` over a temporary `Config` built from steps 2–6. HTTP errors are shown with their hint, and the user may retry or quit.

`KeyboardInterrupt` exits 130 and writes nothing. Nothing is written before step 8.

### `iban.py`
`_COUNTRY_LENGTHS` holds the full SWIFT IBAN registry table (about 89 entries; source URL in a comment). `validate` is unchanged otherwise.

### `mcp_server.py` (new): `enablebanking mcp`
```python
def get_history(accounts: list[str] | None = None, days: int | None = None, date_from: str | None = None,
                date_to: str | None = None, include: list[str] | None = None) -> dict   # api.fetch_history
def consent_status() -> dict                                                            # api.consent_status
def build_server():   # lazy import; tries `mcp.server.fastmcp.FastMCP`, then `mcp.server.MCPServer` (SDK v2 name)
def main(config_path: Path | None) -> int  # ImportError → stderr "pip install 'enablebanking-psd2[mcp]'", exit 2
```
- The tool functions are plain module functions: unit-tested without the SDK, and registered by `build_server()` with `@server.tool()`.
- The descriptions state:
  - get_history is read-only, returns schema v1 and uses the cache; banks may allow 4 unattended requests per account per day, so don't call it repeatedly with `use_cache` off (the parameter isn't exposed)
  - for `consent_required`, the user must run `enablebanking authorize`
- `date_*` arrive as ISO strings and are parsed with `date.fromisoformat`; an invalid value raises `ValueError`, which becomes the tool error.
- `ConfigError` is re-raised as `ValueError(message)`. The SDK turns either into an `isError` result.
- stdout is reserved for JSON-RPC: logging goes to stderr only, at WARNING unless `--verbose`, and `fetch_history` prints nothing.
- The config comes from `--config`, else the `paths` resolution.

### Skills (`skills/`)
**`bank-account-history/SKILL.md`** (rewritten from the 004 version):
- Frontmatter:
  - `name`; `description` (no "Belgian": "the user's own bank accounts connected through Enable Banking (PSD2)")
  - `when_to_use`, `argument-hint`
  - `allowed-tools: Bash(enablebanking history *) Bash(.venv/bin/enablebanking history *) Read`
- Body:
  1. Locate the CLI: `enablebanking` on PATH, else `.venv/bin/enablebanking` (inside the repo checkout). If neither is found, stop and tell the user to run `pipx install enablebanking-psd2` and to use the setup skill.
  2. Run one `enablebanking history … --save`. stdout is the file path, which the agent then reads.
  3. Interpret the statuses and exit codes per `references/contract.md`.
  4. Answer. The 004 rules stay: never `authorize`, no `--no-cache`, mask IBANs, no exfiltration.
- No repo-relative paths other than the optional `.venv` fallback.

**`bank-account-history/references/contract.md`:** amounts (signed decimal strings), dates, ids (`ref:`/`h:`), statuses, `warnings`, `cached`/`stale`, exit codes 0/1/2/3, the summary totals. Loaded only when needed (progressive disclosure).

**`bank-account-setup/SKILL.md`:**
- Frontmatter: `allowed-tools: Bash(enablebanking --version) Bash(enablebanking check*) Bash(enablebanking authorize --status*)`.
- Steps:
  1. Install (`pipx install enablebanking-psd2`).
  2. Register an Enable Banking app (link to the setup guide).
  3. The user runs `! enablebanking init`.
  4. The user runs `! enablebanking authorize`.
  5. The agent runs `enablebanking authorize --status --json` to confirm.
- It never reads `config.toml`, `*.pem` or the state directory, and never runs `init`/`authorize` itself, because they're interactive and log in to a bank.

**Agent Skills portability:**
- The skills are for agents that run locally with a shell: Claude Code and the Agent SDK (via `setting_sources=["project"]` with `.claude/skills`, or the SDK `plugins` option pointing at the repo root).
- **Claude API skill upload is out of scope.** The code-execution sandbox has no network to Enable Banking and no access to the user's key. The docs send API and other-framework agents to the MCP server or the Python API. This narrows R4 AC1 and is recorded as a deviation.

### Plugin and marketplace
```json
// .claude-plugin/plugin.json
{"name": "enablebanking-psd2", "version": "0.2.0", "description": "Read your own bank accounts (PSD2 via Enable Banking) from agents",
 "author": {"name": "Jeroen Ost"}, "repository": "https://github.com/jersteth/enablebanking-psd2", "license": "Apache-2.0"}
// .claude-plugin/marketplace.json
{"name": "enablebanking-psd2", "owner": {"name": "Jeroen Ost"},
 "plugins": [{"name": "enablebanking-psd2", "source": "./", "description": "Agent skills for enablebanking-psd2"}]}
```
- The plugin root is the repo root, so `skills/` is discovered. The installed skills are `enablebanking-psd2:bank-account-history` and `enablebanking-psd2:bank-account-setup`.
- The plugin declares no MCP server, so a Claude Code install doesn't need the `[mcp]` extra.
- The project-local `.claude/skills/bank-account-history` is a relative symlink to `../../skills/bank-account-history`, so there is no second copy (R4 AC6). `.claude/skills/bank-account-setup` gets one too.
- A version test keeps `plugin.json` `version` equal to `__version__`.

### Examples (`examples/`)
- `fetch_history.py`: last 30 days, spending per currency with `Decimal`, and the `consent_required` hint.
- `agent_sdk_agent.py`:
  - `claude_agent_sdk.query` with `ClaudeAgentOptions(plugins=[{"type": "local", "path": REPO_ROOT}], skills=["enablebanking-psd2:bank-account-history"], allowed_tools=["Bash(enablebanking history *)", "Read"])`
  - prompt: "what did I spend in the last 7 days?"
- `mcp.json`: `{"mcpServers": {"enablebanking": {"command": "uvx", "args": ["--from", "enablebanking-psd2[mcp]", "enablebanking", "mcp"]}}}`, plus a Claude Desktop note.

### Docs
- `documentation/agent-integration.md` (new): choosing an entry point (skill / MCP / Python), install, the security rules, the rate limits and cache, privacy (the model provider sees what the agent reads), Windows caveats.
- `enablebanking-setup.md`: rewritten for any user and country ("bring your own app").
- `account-history.md`: `--save`, the new paths, the `enablebanking` command.
- README: developer quick start (`pipx install`, `init`, `authorize`, plugin install, ask).
- `specs/product.md`: scope widened to every Enable Banking country.
- `CLAUDE.md`: the new names and modules.
- `CHANGELOG.md` (new).

### CI and release (`.github/workflows/`)
- **`ci.yml`:** on push and PR, a matrix of `ubuntu-latest` + `macos-latest` × Python 3.11 / 3.14. It runs `pip install -e '.[dev]'`, `pytest`, `python -m build`, and `twine check dist/*`.
- **`release.yml`:** on tag `v*`, build, then `pypa/gh-action-pypi-publish@release/v1` in environment `pypi`, with `permissions: id-token: write`. Trusted publishing, no token.
- A one-time manual step for the owner: add a "pending publisher" on PyPI for `jersteth/enablebanking-psd2`, workflow `release.yml`, environment `pypi`.
- A tag check fails the release if the tag doesn't equal `v{__version__}`.

## Data Models
- `Config` gains: `config_path`, `export_dir`, `country`. `state_dir` is now resolved.
- TOML additions:
  ```toml
  [enablebanking]
  country = "BE"          # env EB_COUNTRY; default for `check` and `banks`
  [paths]                 # optional; relative paths are relative to this config file
  state_dir = "..."       # env ENABLEBANKING_PSD2_STATE_DIR
  export_dir = "..."      # env ENABLEBANKING_PSD2_EXPORT_DIR
  ```
- `consent_status` result: `{"accounts": [{"account": str, "bank": str, "iban_masked": str, "status": "authorized|expiring|consent_required|unknown (...)", "valid_until": str|null}]}`. These are the 004 `_status_row` keys. The result is documented in `references/contract.md` but isn't part of schema v1.
- Schema v1 and the history document: unchanged.

## Error Handling
- **Config resolution:**
  - An explicit or env-named config file that is missing gives `ConfigError` (exit 2).
  - With no config at all and incomplete env, the `ConfigError` lists what's missing and adds "run `enablebanking init`".
- **`init`:**
  - Validation failures re-ask the question, up to 3 times, then exit 2.
  - API errors while searching banks show the `ApiError` hint; the user may retry or quit (exit 1).
  - Ctrl-C exits 130.
  - It never leaves a partial file (one atomic write at the end).
- **`history --save`:** a write failure gives exit 1 with a stderr message (same as `--output`). `--save` together with `--output` is an argparse error (exit 2).
- **MCP:**
  - A missing SDK exits 2 with an install hint.
  - Tool errors (`ConfigError`, a bad date) become MCP errors. A per-account problem stays inside the document, as in 004.
  - An unexpected exception becomes a redacted message (`log.redact`).
- **Shim:** importing `psd2demo` warns once, and its behavior is identical.
- **Skill:**
  - A missing CLI means an install instruction, with no retries.
  - Exit 2 means showing stderr and suggesting the setup skill.
  - `consent_required` means telling the user to run `! enablebanking authorize`.

## Testing Strategy
All tests stay offline (respx / `FakeClient`). Test imports are renamed mechanically to `enablebanking_psd2`; their assertions don't change.
- `test_paths.py`:
  - each precedence level, using `tmp_path` with a fake `HOME` and the `XDG_*` variables via monkeypatch
  - legacy `./config.toml` and `./.psd2demo` win when present
  - relative `[paths]` resolve against the config directory
  - the Windows branch, with `os.name` monkeypatched
- `test_config.py`:
  - env-only config (no file)
  - `country` validation
  - an explicit missing file gives an error
- `test_commands_*`:
  - `check` and `banks` use `cfg.country`
  - the note only appears inside a git work tree
  - `history --save` writes 0600 and stdout is the path
  - `--save` with `--output` gives exit 2
- `test_iban.py`: lengths of non-BE IBANs (NL, DE, FR, LU, a long MT one), valid and invalid.
- `test_api.py`:
  - `consent_status` rows
  - no client created when no account has a stored record
  - `history.fetch_history is api.fetch_history`
- `test_onboarding.py`:
  - a scripted `Prompter` with a `FakeClient` for banks
  - the full happy path: the written TOML parses with `load_config` and is mode 0600; then `check` (via `cli.make_client` monkeypatch)
  - an existing file declined; group-readable key rejected; invalid UUID re-asked; Ctrl-C writes nothing
  - the TOML escaping (quotes, backslashes in paths)
- `test_mcp_server.py`:
  - the tool functions call `api` (monkeypatched), and their errors map
  - with the `mcp` extra installed: `build_server()` lists exactly 2 tools with the expected names and input schemas
  - the server never writes to stdout (`capsys`)
- `test_skill.py` (generalized):
  - each `skills/*/SKILL.md` frontmatter parses, with the required keys
  - every `enablebanking …` command line and inline flag in each body parses with `parse_args`
  - `allowed-tools` only allows `history` (history skill) or read-only commands (setup skill)
  - no skill body mentions `config.toml`/`.pem` as something to read
  - the `.claude/skills/*` symlinks resolve into `skills/`
- `test_packaging.py`:
  - the `psd2demo` shim warns and aliases the same module objects
  - `plugin.json` version equals `__version__`
  - `marketplace.json` has the required fields
  - `contract.schema_path()` exists and the example is in sync (the existing schema test, new path)
- One multi-country fixture: an NL account with USD transactions through `history`, valid against the schema, with per-currency `totals` (R8 AC4).
- Not automated, done by the owner:
  - `claude plugin validate .`
  - a clean-environment install from the wheel (`pipx install dist/*.whl`, then `init` → `authorize` → `history --save` from `/tmp`)
  - a Claude Code plugin install in an empty folder
  - the Agent SDK example
  - MCP Inspector: `npx @modelcontextprotocol/inspector enablebanking mcp`

## Performance Considerations
- The API call budget is unchanged: the cache, a lazy client, and one session check per bank per run. `consent_status` makes at most one `GET /sessions/{id}` per stored session.
- The MCP server is a long-lived process: each tool call loads the config fresh (so edits apply) and creates and closes its client per call. No connection is reused across calls; this keeps the 004 lifecycle and doesn't hold a JWT longer than needed.
- `paths` resolution is a handful of `stat` calls, with no subprocess (the git work tree check walks parents looking for `.git`).
- `init` fetches the bank list once per search and filters it locally.

## Security Considerations
- **Bring your own app:** no shared credentials, the project hosts nothing, and there are no network calls other than to Enable Banking. The PyPI release uses trusted publishing (OIDC), so no token is stored.
- **File permissions:** the config (written by `init`), state and exports are 0600 in 0700 directories. `init` refuses a group- or world-readable key and never echoes it.
- **Agent surfaces are read-only:**
  - the skills' `allowed-tools` only pre-approve `history` (history skill) and status/check commands (setup skill)
  - the MCP server has no consent, revoke or write tool
  - interactive consent always stays a human step (`! enablebanking authorize`)
- **No secrets to agents:** the skill bodies forbid reading `config.toml`, `*.pem` and the state directory, and the tests check this. `references/contract.md` contains no real data.
- **Redaction** (`log.RedactingFilter`, `set_own_ibans`) applies unchanged to CLI, API and MCP logging. The MCP server logs only to stderr.
- **Supply chain:**
  - the core keeps 2 runtime dependencies, and `mcp` is optional
  - CI pins the actions to major tags, with the PyPI publish action from `pypa`
  - the shim has no import-time side effects beyond aliasing
- **Privacy:** the docs state that whatever the agent reads goes to its model provider. They recommend `--include` only when needed, and that the agent shouldn't print full IBANs or transaction lists unless asked.

## Monitoring and Observability
- No telemetry.
- CLI: the existing `--verbose` (redacted HTTP debug on stderr), `api_calls`/`cached`/`stale` per account in the document, and `enablebanking --version`.
- MCP: stderr logging (warnings by default, `--verbose` for debug). Each tool call logs one INFO line: the tool, the accounts and the resulting status counts. No amounts and no IBANs.
- CI shows test and build status for every push. The release workflow gives traceable builds via tags, and PyPI shows the provenance from trusted publishing.
