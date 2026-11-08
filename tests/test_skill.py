"""The agent skills: valid frontmatter, every command they tell the agent to run is accepted, no secrets access."""

import json
import re
import shlex
from pathlib import Path

import pytest

import enablebanking_psd2
from enablebanking_psd2 import contract
from enablebanking_psd2.cli import parse_args

ROOT = Path(__file__).parent.parent
SKILLS = sorted((ROOT / "skills").glob("*/SKILL.md"))
CLI = r"(?:\.venv/bin/)?enablebanking"


def _frontmatter(skill: Path) -> dict[str, str]:
    text = skill.read_text()
    assert text.startswith("---\n")
    return dict(line.split(": ", 1) for line in text.split("---\n")[1].strip().splitlines())


def _commands(skill: Path) -> list[str]:
    """Command lines in ``` blocks (the arguments after `enablebanking`)."""
    return re.findall(rf"^{CLI} (.+)$", skill.read_text(), flags=re.M)


def _inline(skill: Path) -> list[str]:
    """Every `enablebanking ...` / `! enablebanking ...` mentioned in backticks (what the user or agent may run)."""
    return re.findall(rf"`(?:! )?{CLI} ([^`]+)`", skill.read_text())


def test_both_skills_exist():
    assert [s.parent.name for s in SKILLS] == ["bank-account-history", "bank-account-setup"]


@pytest.mark.parametrize("skill", SKILLS, ids=lambda s: s.parent.name)
def test_frontmatter(skill):
    fm = _frontmatter(skill)
    assert fm["name"] == skill.parent.name
    assert {"description", "when_to_use", "allowed-tools"} <= fm.keys()
    assert len(fm["description"]) + len(fm["when_to_use"]) < 1536
    assert "<" not in fm["description"] and ">" not in fm["description"]
    assert "Belgi" not in skill.read_text()  # any country


@pytest.mark.parametrize("skill", SKILLS, ids=lambda s: s.parent.name)
def test_every_command_line_parses(skill):
    cmds = [*_commands(skill), *_inline(skill)]
    assert cmds
    for cmd in cmds:
        if cmd.strip() == "--version":
            continue
        parse_args(shlex.split(cmd))
    for option in re.findall(r"`(--(?:days|from|account|include)[^`]*)`", skill.read_text()):
        if "<" not in option:
            parse_args(["history", *shlex.split(option)])


def test_history_skill_pre_approves_history_only():
    skill = ROOT / "skills" / "bank-account-history" / "SKILL.md"
    fm = _frontmatter(skill)
    assert fm["allowed-tools"] == "Bash(enablebanking history *) Bash(.venv/bin/enablebanking history *) Read"
    assert {c.split()[0] for c in _commands(skill)} == {"history"}
    assert all("--save" in c for c in _commands(skill))  # the fixed, path-printing form
    assert "--no-cache" not in " ".join(_commands(skill))


def test_setup_skill_pre_approves_read_only_commands_and_never_runs_interactive_ones():
    skill = ROOT / "skills" / "bank-account-setup" / "SKILL.md"
    allowed = re.findall(r"Bash\(([^)]*)\)", _frontmatter(skill)["allowed-tools"])
    assert allowed == ["enablebanking --version", "enablebanking check", "enablebanking authorize --status *"]
    assert "Read" not in _frontmatter(skill)["allowed-tools"].split()
    text = skill.read_text()
    agent_runs = re.findall(rf"(?<![!] )`{CLI} ([^\s`]+)", text)  # backticked without the `! ` prefix = the agent runs it
    assert agent_runs and set(agent_runs) <= {"--version", "check", "authorize"}
    assert "! enablebanking init" in text and "! enablebanking authorize" in text  # the user runs these


@pytest.mark.parametrize("skill", SKILLS, ids=lambda s: s.parent.name)
def test_skills_forbid_reading_secrets(skill):
    text = skill.read_text()
    assert re.search(r"[Nn]ever read the user's config file, private key \(`\*\.pem`\) or state directory", text)
    for line in text.splitlines():  # no instruction to cat/read those files
        assert not re.search(r"(cat|Read|read) .*(config\.toml|\.pem|\.psd2demo)", line) or "never" in line.lower()


def test_contract_reference_covers_statuses_and_exit_codes():
    ref = (ROOT / "skills" / "bank-account-history" / "references" / "contract.md").read_text()
    for status in contract.STATUSES:
        assert f"`{status}`" in ref
    for code in ("0", "1", "2", "3"):
        assert re.search(rf"(^|\s){code} ", ref.split("## Exit codes")[1])
    assert "schema v1" in ref and contract.SCHEMA_VERSION == "1"


def test_project_local_skills_are_symlinks_into_skills():
    for name in ("bank-account-history", "bank-account-setup"):
        link = ROOT / ".claude" / "skills" / name
        assert link.is_symlink() and link.resolve() == (ROOT / "skills" / name).resolve()
        assert (link / "SKILL.md").is_file()


def test_plugin_manifest_and_marketplace():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert plugin["version"] == enablebanking_psd2.__version__
    assert {"name", "owner", "plugins"} <= market.keys()
    (entry,) = market["plugins"]
    assert entry["name"] == plugin["name"] and entry["source"] == "./"  # entry and manifest names must agree
    assert (ROOT / "skills").is_dir()  # the plugin root is the repo root
