"""Logging setup with secret redaction."""

import logging
import re
import sys

import httpx

from psd2demo.iban import mask_others

LOGGER = "psd2demo"
_PATTERNS = [
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"), "Bearer ***"),
    (re.compile(r"\b(code|state)=[^&\s\"']+"), r"\1=***"),
    (re.compile(r"\"(code|state)\"\s*:\s*\"[^\"]*\""), r'"\1": "***"'),
]


class RedactingFilter(logging.Filter):
    def __init__(self, keep_ibans: frozenset[str] = frozenset()):
        super().__init__()
        self.keep_ibans = keep_ibans  # the user's own configured accounts; every other IBAN is masked

    def redact(self, text: str) -> str:
        for pattern, repl in _PATTERNS:
            text = pattern.sub(repl, text)
        return mask_others(text, self.keep_ibans)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = None
        return True


_FILTER = RedactingFilter()


def set_own_ibans(ibans) -> None:
    """IBANs of the user's configured accounts stay readable in logs; all others are masked."""
    _FILTER.keep_ibans = frozenset(ibans)


def setup_logging(verbose: bool) -> logging.Logger:
    logger = logging.getLogger(LOGGER)
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(_FILTER)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if verbose else logging.WARNING)
    logger.propagate = False
    return logger


def truncate_body(text: str, limit: int = 2048) -> str:
    """Cut at ``limit`` but drop a trailing partial word, so a split IBAN can't escape redaction."""
    if len(text) <= limit:
        return text
    return re.sub(r"[A-Za-z0-9]+$", "", text[:limit]) + "…"


def http_event_hooks() -> dict:
    """httpx hooks logging each response at DEBUG (body truncated to 2 KB)."""
    logger = logging.getLogger(LOGGER)

    def on_response(resp: httpx.Response) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        resp.read()
        logger.debug("%s %s -> %d (%.0f ms) %s", resp.request.method, resp.request.url, resp.status_code,
                     resp.elapsed.total_seconds() * 1000, truncate_body(resp.text))

    return {"response": [on_response]}
