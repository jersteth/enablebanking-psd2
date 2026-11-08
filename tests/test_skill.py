"""The Claude Code skill: valid frontmatter, and every command it tells the agent to run is accepted."""

import re
import shlex
from pathlib import Path

from psd2demo.cli import parse_args

SKILL = Path(__file__).parent.parent / ".claude" / "skills" / "bank-account-history" / "SKILL.md"


def _frontmatter() -> dict[str, str]:
    text = SKILL.read_text()
    assert text.startswith("---\n")
    block = text.split("---\n")[1]
    return dict(line.split(": ", 1) for line in block.strip().splitlines())


def test_frontmatter():
    fm = _frontmatter()
    assert fm["name"] == "bank-account-history"
    assert fm["allowed-tools"] == "Bash(.venv/bin/psd2-demo history *) Read"  # history only, never authorize
    assert len(fm["description"]) + len(fm["when_to_use"]) < 1536
    assert "<" not in fm["description"] and ">" not in fm["description"]


def test_every_history_command_parses():
    commands = re.findall(r"^\.venv/bin/psd2-demo (history .+)$", SKILL.read_text(), flags=re.M)
    inline = re.findall(r"`(--(?:days|from|account|include)[^`]*)`", SKILL.read_text())
    assert len(commands) >= 2
    for cmd in commands:
        assert parse_args(shlex.split(cmd)).cmd == "history"
    for option in inline:
        if "<" not in option:
            parse_args(["history", *shlex.split(option)])


def test_skill_never_runs_interactive_or_destructive_commands():
    runs = re.findall(r"^\.venv/bin/psd2-demo (\S+)", SKILL.read_text(), flags=re.M)
    assert set(runs) == {"history"}
