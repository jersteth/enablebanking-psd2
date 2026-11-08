# 005 – Agent Skill Package: Tasks

Refs: `requirements.md` (R1–R8), `design.md`. Prerequisite: 004 (implemented). Branch: `feature/005-agent-skill-package`. Mark tasks `[x]` once they are implemented and their tests pass. Every phase ends with a full `pytest` run that stays offline.

## Phase 1 – Rename and Packaging
**Goal**: the code ships as `enablebanking-psd2` with all three command names; existing behavior is unchanged.
**Verify (user)**:
1. `.venv/bin/pip install -e '.[dev]'`
2. `.venv/bin/enablebanking --version` prints `0.2.0`, and `.venv/bin/psd2-demo check` still works.
3. `python -W error::DeprecationWarning -c "import psd2demo"` fails with the deprecation warning.

- [ ] 1.1 `git mv src/psd2demo src/enablebanking_psd2`. Rename every import in `src/` and `tests/` mechanically. Set `log.LOGGER = "enablebanking_psd2"` and `__version__ = "0.2.0"`. Test: the existing suite (assertions unchanged).
- [ ] 1.2 `pyproject.toml` as in the design:
  - name, dynamic version, Apache-2.0, URLs, classifiers
  - scripts `enablebanking`, `enablebanking-psd2`, `psd2-demo`
  - the `mcp` extra; dev extras `mcp`, `build`, `twine`
  - wheel packages `src/enablebanking_psd2` and `src/psd2demo`

  Reinstall the venv. Test: `test_packaging.py::test_entry_points` (all three scripts map to `cli:main`, read via `importlib.metadata`).
- [ ] 1.3 `src/psd2demo/__init__.py` shim: a `DeprecationWarning` plus a `sys.meta_path` alias finder, so `psd2demo.x` is the same object as `enablebanking_psd2.x`. Test: `test_packaging.py::test_shim_aliases_same_modules` (warns; `psd2demo.cli is enablebanking_psd2.cli`; a monkeypatch through one name is visible through the other).
- [ ] 1.4 `git mv schemas src/enablebanking_psd2/schemas`. Add `contract.schema_path(version="1")` via `importlib.resources`, and set the `$id` to the new URL. Update `tests/history_docs.py`, `test_schema.py` and the doc links. Test: `test_schema.py` (validation plus the example kept in sync, via `schema_path`), and `test_packaging.py::test_wheel_contains_schemas` (`python -m build --wheel` into `tmp_path`, then list the zip).
- [ ] 1.5 `cli.py`: the `prog` from the `argv[0]` basename, and the description "PSD2 account access via Enable Banking". Update the package docstring. Test: `test_cli.py` (help text has no "Belgian"; `--version`).

## Phase 2 – Location Independence and Any Country
**Goal**: the CLI and the API work from any directory, from an XDG config or from env only, for any Enable Banking country.
**Verify (user)**:
1. `mkdir -p ~/.config/enablebanking-psd2 && cp config.toml ~/.config/enablebanking-psd2/`, then `cd /tmp && ~/dev/claude/psd2-test/.venv/bin/enablebanking authorize --status` shows the same status as in the repo.
2. `enablebanking banks --country NL --filter ing` lists Dutch banks.
3. In the repo, `enablebanking history --days 7 --save` prints `…/exports/history.json`.

- [ ] 2.1 `paths.py`: `config_file`, `default_config_target`, `state_dir`, `export_dir`, `user_*_dir` (POSIX XDG, plus the Windows `%APPDATA%` branch) and `in_git_worktree`. Test: `test_paths.py` (every precedence level, legacy `./` locations winning, relative `[paths]` resolving against the config directory, the Windows branch with `os.name` monkeypatched, `in_git_worktree`).
- [ ] 2.2 `config.load_config(..., cwd=None)`:
  - file resolution via `paths`; a missing explicit or env-named file is an error, a missing default isn't
  - new `Config` fields `config_path`, `state_dir` (resolved), `export_dir`, `country` (`[enablebanking] country` / `EB_COUNTRY`, 2 letters)
  - the `[paths]` section
  - the new `NO_ACCOUNTS` text, and an "run `enablebanking init`" hint when there is no config and the env is incomplete

  Test: `test_config.py` (env-only config; country valid and invalid; `[paths]`; missing explicit file; the existing tests unchanged).
- [ ] 2.3 Replace every `Path(".psd2demo")` and `EXPORTS_DIR` use with `cfg.state_dir`/`cfg.export_dir`. `_outside_exports_note(path, cfg)` warns only inside a git work tree and outside `cfg.export_dir`. Test: `test_commands_run.py` / `test_cli_history.py` (the note inside a repo vs. `tmp_path` without `.git`).
- [ ] 2.4 Country: `check` without an account uses `get_aspsps(cfg.country)` with the message "N banks in XX"; `banks --country` defaults to `cfg.country`. Test: `test_commands_check.py`, `test_commands_banks.py`.
- [ ] 2.5 `iban._COUNTRY_LENGTHS`: the full IBAN registry table, with its source in a comment. Test: `test_iban.py` (NL, DE, FR, LU, MT, NO valid; wrong lengths rejected; BE unchanged).
- [ ] 2.6 `history --save` (mutually exclusive with `--output`): an atomic 0600 write to `cfg.export_dir/history.json`; stdout is the absolute path. Test: `test_cli_history.py` (file mode, stdout equals the path, exit codes unchanged, `--save --output` gives exit 2, a write failure gives exit 1).
- [ ] 2.7 Multi-country fixture: an NL account with USD and EUR transactions run through `history`. Test: `test_history.py` (the document is schema-valid; `totals` per currency; `currency` is the main one).

## Phase 3 – Public API and Onboarding
**Goal**: a stable Python API with `consent_status`, and `enablebanking init` taking a new user to a working config.
**Verify (user)**:
1. `HOME=$(mktemp -d) .venv/bin/enablebanking init`: enter your app ID, key, a bank search and your IBAN. It writes `~/.config/enablebanking-psd2/config.toml` (mode 0600) and `check` passes.
2. `python -c "from enablebanking_psd2 import consent_status; print(consent_status())"` in the repo prints your account's status.

- [ ] 3.1 `api.py`:
  - move `fetch_history` there, with `history.fetch_history` becoming an alias
  - add `consent_status`
  - add the shared `_session` client context manager
  - re-export both from `enablebanking_psd2/__init__.py`

  Split `commands._authorize_status` into a pure `consent_rows(ctx, targets)`, used by `authorize --status` and the API. Test: `test_api.py` (rows per status; no client created without stored records; `ConfigError` only; the alias is identical); `test_history_api.py` unchanged.
- [ ] 3.2 `onboarding.py`:
  - `Prompter`, `run(args, env, prompter, client_factory)`
  - the step flow from the design: target and overwrite, app ID, key (mode/PEM), redirect URL, PSU IP, country, account loop, then `_render_toml`, an atomic write and `check`

  Test: `test_onboarding.py`:
  - happy path: the TOML loads with `load_config`, the file is mode 0600, and `check` runs via a monkeypatched `cli.make_client`
  - an existing file declined writes nothing; `--force` overwrites
  - a group-readable key gives exit 2 with the chmod hint
  - an invalid UUID is re-asked, then exit 2 after 3 attempts
  - an invalid IBAN is re-asked; a duplicate account name is rejected
  - Ctrl-C gives exit 130 and writes nothing
  - TOML escaping of quotes and backslashes
  - the key contents never appear in the output
- [ ] 3.3 CLI `init` subparser (`--config`, `--force`), dispatched before `_execute`, like `history`. Test: `test_cli.py` (it parses; `main(["init", …])` with an injected prompter).

## Phase 4 – Portable Skills, Plugin and Marketplace
**Goal**: the skills work from any folder through the Claude Code plugin, and still work project-locally in this repo.
**Verify (user)**:
1. `claude plugin validate .` passes.
2. In an empty folder: `claude plugin marketplace add ~/dev/claude/psd2-test`, then `claude plugin install enablebanking-psd2@enablebanking-psd2`. Ask "what did I spend in the last 7 days?": the skill runs `enablebanking history --days 7 --save` and answers.
3. In the repo, the same question still works.

- [ ] 4.1 `skills/bank-account-history/SKILL.md`, rewritten per the design:
  - the frontmatter's `allowed-tools` covers `enablebanking history *` and the `.venv` fallback
  - CLI location, then one `--save` run, then read the printed path
  - status handling, and the 004 rules

  Plus `references/contract.md`. Remove `.claude/skills/bank-account-history/SKILL.md` and replace the folder with a relative symlink to `../../skills/bank-account-history`.
- [ ] 4.2 `skills/bank-account-setup/SKILL.md`:
  - install, register the app, `! enablebanking init`, `! enablebanking authorize`, confirm with `authorize --status --json`
  - read-only `allowed-tools`; it never reads the config, key or state

  Plus the symlink `.claude/skills/bank-account-setup`.
- [ ] 4.3 Generalize `tests/test_skill.py` over `skills/*/SKILL.md`:
  - the frontmatter's required keys
  - every `enablebanking`/`.venv/bin/enablebanking` command line and inline flag parses
  - `allowed-tools` allows only `history` (history skill) or `--version`/`check`/`authorize --status` (setup skill)
  - no instruction to read `config.toml`, `*.pem` or the state directory
  - the `.claude/skills/*` symlinks resolve into `skills/`
  - `references/contract.md` lists every status in `contract.STATUSES` and exit codes 0/1/2/3
- [ ] 4.4 `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` per the design. Test: `test_packaging.py` (`plugin.json` version equals `__version__`; the marketplace's required fields; the plugin entry name equals the manifest name; `source` is `"./"`).

## Phase 5 – MCP Server and Examples
**Goal**: agents outside Claude can use the data through MCP; developers have working examples.
**Verify (user)**:
1. `npx @modelcontextprotocol/inspector .venv/bin/enablebanking mcp` lists `get_history` and `consent_status`. Calling `consent_status` returns your account, and calling `get_history` with `days: 7` returns a schema-v1 document.
2. `.venv/bin/python examples/fetch_history.py` prints your spending.
3. Optional, needs an Anthropic API key: `examples/agent_sdk_agent.py` answers "what did I spend in the last 7 days?".

- [ ] 5.1 Check the installed `mcp` SDK's server class (`mcp.server.fastmcp.FastMCP` vs `mcp.server.MCPServer`) and its stdio `run`. Pin the `mcp` lower bound accordingly in `pyproject.toml`.
- [ ] 5.2 `mcp_server.py`:
  - the plain tool functions `get_history` and `consent_status`
  - `build_server()` with a lazy import and the descriptions from the design
  - `main(config_path, verbose)`: logging to stderr; without the SDK, exit 2 with the install hint

  CLI `mcp` subparser (`--config`, `--verbose`). Test: `test_mcp_server.py`:
  - the tools call `api` with parsed dates
  - `ConfigError` and a bad date become `ValueError`
  - an unexpected error comes back redacted
  - `build_server()` registers exactly 2 tools with the expected names and parameters
  - nothing goes to stdout
  - a missing SDK (simulated `ImportError`) gives exit 2
- [ ] 5.3 In-process MCP round trip: a client session over the SDK's memory or stdio transport against `build_server()` with a monkeypatched `api`, calling both tools. Test: `test_mcp_server.py::test_round_trip`. Skip it if the SDK has no in-memory transport, and note that in the deviations.
- [ ] 5.4 `examples/fetch_history.py`, `examples/agent_sdk_agent.py` (plugins local path; `skills` restricted; `allowed_tools`) and `examples/mcp.json`. Test: `test_examples.py` (both `.py` files compile; `fetch_history.py` runs against an injected `FakeClient` via env and an injected config; `mcp.json` parses).

## Phase 6 – Documentation, CI and Release
**Goal**: integrator docs and a reproducible PyPI release.
**Verify (user)**:
1. A push to GitHub shows green CI on Linux and macOS.
2. On PyPI, add a pending trusted publisher (`jersteth/enablebanking-psd2`, `release.yml`, environment `pypi`), then `git tag v0.2.0 && git push --tags` publishes. On a clean machine, `pipx install enablebanking-psd2` followed by `enablebanking --version` prints `0.2.0`.

- [ ] 6.1 `documentation/agent-integration.md` (entry points, install, security rules, rate limits, privacy, Windows caveats). Update `account-history.md` (`--save`, paths, the `enablebanking` command), rewrite `enablebanking-setup.md` for any user and country, and update `documentation/index.md`.
- [ ] 6.2 README developer quick start; `specs/product.md` (wider scope); `CLAUDE.md` (names, modules, paths); `config.example.toml` (`country`, `[paths]`); `CHANGELOG.md` (0.2.0: the rename, paths, `--save`, `init`, skills, plugin, MCP, country).
- [ ] 6.3 `.github/workflows/ci.yml`: a matrix of ubuntu and macOS × Python 3.11/3.14, running `pip install -e '.[dev]'`, `pytest`, `python -m build` and `twine check`.
- [ ] 6.4 `.github/workflows/release.yml`: on a tag `v*`, check that the tag equals `v{__version__}`, build, and run `pypa/gh-action-pypi-publish@release/v1` with `id-token: write` in environment `pypi`. Test: `test_packaging.py::test_release_workflow` (the YAML parses; `id-token: write`; no `password:`/token secret; the tag check is present).
- [ ] 6.5 Final check:
  - full `pytest`, plus `python -m build` and `twine check`
  - a secret and IBAN grep over the repo and the built sdist (example IBANs only, no `config.toml`, no `*.pem`, no `.psd2demo`)
  - `claude plugin validate .`
  - a wheel install in a fresh venv under `/tmp`, then `enablebanking --version` and `enablebanking history --help`

## Verification Status
Not started.

## Design Deviations
None yet.
