"""Logging setup with secret redaction."""

import logging
import re
import sys

import httpx

from psd2demo.iban import IBAN_RE, mask

LOGGER = "psd2demo"
_IBAN_RE = re.compile(rf"\b{IBAN_RE.pattern}\b")
_PATTERNS = [
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"), "Bearer ***"),
    (re.compile(r"\b(code|state)=[^&\s\"']+"), r"\1=***"),
    (re.compile(r"\"(code|state)\"\s*:\s*\"[^\"]*\""), r'"\1": "***"'),
]


class RedactingFilter(logging.Filter):
    def __init__(self, target_iban: str | None = None):
        super().__init__()
        self.target_iban = target_iban

    def redact(self, text: str) -> str:
        for pattern, repl in _PATTERNS:
            text = pattern.sub(repl, text)
        return _IBAN_RE.sub(lambda m: m.group(0) if m.group(0) == self.target_iban else mask(m.group(0)), text)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = None
        return True


def setup_logging(verbose: bool, target_iban: str | None = None) -> logging.Logger:
    logger = logging.getLogger(LOGGER)
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(RedactingFilter(target_iban))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if verbose else logging.WARNING)
    logger.propagate = False
    return logger


def http_event_hooks() -> dict:
    """httpx hooks logging each response at DEBUG (body truncated to 2 KB)."""
    logger = logging.getLogger(LOGGER)

    def on_response(resp: httpx.Response) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        resp.read()
        logger.debug("%s %s -> %d (%.0f ms) %s", resp.request.method, resp.request.url, resp.status_code,
                     resp.elapsed.total_seconds() * 1000, resp.text[:2048])

    return {"response": [on_response]}
