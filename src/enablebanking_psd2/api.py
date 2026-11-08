"""The public Python API: what applications, the MCP server and the examples import.

Both functions read the config like the CLI does (see ``paths``), print nothing, never prompt and raise only
:class:`~enablebanking_psd2.errors.ConfigError` (and KeyboardInterrupt); per-account problems are reported in
the returned data.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Mapping, Sequence

from enablebanking_psd2.bootstrap import load_signer, make_client
from enablebanking_psd2.commands import consent_rows
from enablebanking_psd2.config import NO_ACCOUNTS, load_config
from enablebanking_psd2.errors import ConfigError
from enablebanking_psd2.flow import Context, StepReporter
from enablebanking_psd2.history import fetch_history
from enablebanking_psd2.log import set_own_ibans
from enablebanking_psd2.session_store import SessionStore

__all__ = ["fetch_history", "consent_status"]


class _LazyClient:
    """Creates the real client on first use, so a run that needs no network call never builds a signer."""

    psu_headers: dict = {}  # Context copies these at construction; status checks never use PSU headers

    def __init__(self, factory):
        self._factory, self._client = factory, None

    def __getattr__(self, name):
        if self._client is None:
            self._client = self._factory()
        return getattr(self._client, name)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


def consent_status(config_path=None, accounts: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None,
                   client=None, now: datetime | None = None) -> dict:
    """Per account: ``authorized`` / ``expiring`` (< 7 days left) / ``consent_required`` / ``unknown (...)``.

    Result: ``{"accounts": [{"account", "bank", "iban_masked", "status", "valid_until"}, ...]}``. Starts no
    consent; makes at most one ``GET /sessions/{id}`` per stored session (none without a stored session).
    """
    cfg = load_config(config_path, os.environ if env is None else env, {})
    targets = cfg.select(accounts)
    if not targets:
        raise ConfigError([NO_ACCOUNTS])
    set_own_ibans(a.iban for a in cfg.accounts.values())
    now = now or datetime.now(timezone.utc)
    lazy = client if client is not None else _LazyClient(lambda: make_client(cfg, load_signer(cfg),
                                                                              retry_rate_limits=False))
    try:
        ctx = Context(cfg=cfg, client=lazy, store=SessionStore(cfg.state_dir / "session.json"),
                      reporter=StepReporter(quiet=True), today=now.astimezone().date(), now=now)
        return {"accounts": consent_rows(ctx, targets)}
    finally:
        if client is None:
            lazy.close()
