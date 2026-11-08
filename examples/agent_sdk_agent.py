"""A Claude Agent SDK agent that answers questions about your bank accounts with the bank-account-history skill.

    pip install claude-agent-sdk enablebanking-psd2
    enablebanking init && enablebanking authorize     # once
    ANTHROPIC_API_KEY=... python agent_sdk_agent.py "what did I spend in the last 7 days?"

The skill lives in this repository's skills/ folder, which is also a Claude Code plugin; the agent loads it
through the SDK's `plugins` option. Only `enablebanking history` and file reads are pre-approved, so the agent
cannot start a bank login, and it cannot see your config or private key (the skill forbids reading them).
Whatever the agent reads goes to its model provider; ask for balances/details only when you need them.
"""

import asyncio
import sys
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

REPO_ROOT = Path(__file__).resolve().parent.parent  # the plugin root (contains .claude-plugin/ and skills/)


def options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        plugins=[{"type": "local", "path": str(REPO_ROOT)}],
        skills=["enablebanking-psd2:bank-account-history"],
        allowed_tools=["Bash(enablebanking history *)", "Read"],
        max_turns=10,
    )


async def ask(question: str) -> str:
    answer = ""
    async for message in query(prompt=question, options=options()):
        if isinstance(message, ResultMessage) and message.subtype == "success":
            answer = message.result
    return answer


if __name__ == "__main__":
    print(asyncio.run(ask(" ".join(sys.argv[1:]) or "What did I spend in the last 7 days?")))
