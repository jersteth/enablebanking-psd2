"""Read-only MCP server (stdio) over the public API: ``enablebanking mcp``.

Two tools, ``get_history`` and ``consent_status``. There is no tool that starts a consent, revokes a session or
writes a file; the interactive bank login stays a human step (``enablebanking authorize``). stdout carries only
the protocol; logging goes to stderr. The ``mcp`` package is an optional extra (``enablebanking-psd2[mcp]``).
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path
from typing import Sequence

from enablebanking_psd2 import api
from enablebanking_psd2.errors import ConfigError
from enablebanking_psd2.flow import EXIT_CONFIG
from enablebanking_psd2.log import redact, setup_logging

log = logging.getLogger(__name__)
INSTALL_HINT = "the MCP server needs the 'mcp' package: pip install 'enablebanking-psd2[mcp]'"


def _date(value: str | None, name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}") from None


def _guard(call):
    """Run ``call``; ConfigError and unexpected errors become a plain ValueError the MCP layer reports."""
    try:
        return call()
    except ConfigError as exc:
        raise ValueError(exc.message) from None
    except ValueError:
        raise
    except Exception as exc:  # never leak a raw traceback or an unredacted IBAN to the client
        raise ValueError(redact(f"{type(exc).__name__}: {exc}")) from None


def get_history(accounts: Sequence[str] | None = None, days: int | None = None, date_from: str | None = None,
                date_to: str | None = None, include: Sequence[str] | None = None, *,
                config_path: Path | None = None) -> dict:
    """The tool body: the schema-v1 history document (see ``api.fetch_history``)."""
    doc = _guard(lambda: api.fetch_history(config_path, accounts=list(accounts) if accounts else None, days=days,
                                           date_from=_date(date_from, "date_from"), date_to=_date(date_to, "date_to"),
                                           include=list(include) if include is not None else None))
    log.info("get_history: %s", doc["summary"]["accounts"])
    return doc


def consent_status(*, config_path: Path | None = None) -> dict:
    """The tool body: per configured account authorized / expiring / consent_required (see ``api``)."""
    result = _guard(lambda: api.consent_status(config_path))
    log.info("consent_status: %d account(s)", len(result["accounts"]))
    return result


def build_server(config_path: Path | None = None):
    """The MCP server with both tools registered. Works with the ``mcp`` 1.x (FastMCP) and 2.x (MCPServer) SDKs."""
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2
    except ImportError:
        from mcp.server.fastmcp import FastMCP as Server  # mcp 1.x
    from mcp.types import ToolAnnotations

    try:
        from mcp.server.mcpserver.exceptions import ToolError  # mcp >= 2
    except ImportError:
        from mcp.server.fastmcp.exceptions import ToolError  # mcp 1.x

    def reported(call):  # an anticipated failure: the client sees our message, not a generic "tool crashed"
        try:
            return call()
        except ValueError as exc:
            raise ToolError(str(exc)) from None

    server = Server("enablebanking")
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

    @server.tool(name="get_history", annotations=read_only)
    def get_history_tool(accounts: list[str] | None = None, days: int | None = None, date_from: str | None = None,
                         date_to: str | None = None, include: list[str] | None = None) -> dict:
        """Transactions (and optionally balances, account details) of the user's configured bank accounts.

        Read-only. Returns the versioned JSON document: per account a status (ok, consent_required,
        rate_limited, error), transactions newest first with signed decimal-string amounts, and per-currency
        totals. Results are cached (60 minutes by default) because banks may allow only 4 unattended requests per
        account per day: do not call repeatedly for the same question. `days` (default from config, usually 30)
        or `date_from`/`date_to` (YYYY-MM-DD) select the period; `include` may contain balances, details, raw.
        An account with status consent_required needs the user to run `enablebanking authorize` themselves.
        """
        return reported(lambda: get_history(accounts, days, date_from, date_to, include, config_path=config_path))

    @server.tool(name="consent_status", annotations=read_only)
    def consent_status_tool() -> dict:
        """Per configured account: authorized, expiring (less than 7 days left), consent_required or unknown.

        Read-only; starts no bank login. Use it to tell the user whether `enablebanking authorize` is needed.
        """
        return reported(lambda: consent_status(config_path=config_path))

    return server


def main(config_path: Path | None = None, verbose: bool = False) -> int:
    try:
        server = build_server(config_path)
    except ImportError:
        print(INSTALL_HINT, file=sys.stderr)
        return EXIT_CONFIG
    setup_logging(verbose)  # stderr only: stdout is the protocol
    server.run("stdio")
    return 0
