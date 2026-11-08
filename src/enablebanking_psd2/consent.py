"""PSD2 consent helpers: state, validity, browser, redirect capture and parsing."""

import hmac
import secrets
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
import socketserver
from typing import Callable
from urllib.parse import parse_qs, urlparse

from enablebanking_psd2.errors import CallbackTimeout, ConsentDenied, ConsentError, StateMismatch

SAFETY_MARGIN = timedelta(seconds=60)
_DONE_HTML = (b"<!doctype html><html><body><h1>Authorization received</h1>"
              b"<p>You can close this tab and return to the terminal.</p></body></html>")


def new_state() -> str:
    return secrets.token_urlsafe(32)


def compute_valid_until(now: datetime, consent_days: int | None, max_validity_s: int | None) -> datetime:
    """``consent_days`` capped at the bank's maximum; None means the maximum itself (needs ``max_validity_s``)."""
    seconds = max_validity_s if consent_days is None else consent_days * 86400
    if max_validity_s:
        seconds = min(seconds, max_validity_s)
    return now + timedelta(seconds=seconds) - SAFETY_MARGIN


def open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except webbrowser.Error:
        pass


def parse_callback(url: str, expected_state: str) -> str:
    """Return the authorization code from a redirect URL, validating ``state``."""
    query = parse_qs(urlparse(url.strip()).query)
    first = lambda k: (query.get(k) or [None])[0]  # noqa: E731
    if first("error"):
        raise ConsentDenied(first("error"), first("error_description"))
    state = first("state")
    if not state or not hmac.compare_digest(state, expected_state):
        raise StateMismatch()
    code = first("code")
    if not code:
        raise ConsentError("redirect URL contains no authorization code",
                           hint="paste the complete URL from the browser address bar")
    return code


def prompt_stderr(prompt: str) -> str:
    """Like ``input()``, but the prompt always goes to stderr so stdout can carry --json output."""
    sys.stderr.write(prompt)
    sys.stderr.flush()
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return line.rstrip("\n")


def capture_paste(timeout: int, prompt_fn: Callable[[str], str] | None = None) -> str:
    """Ask the user to paste the redirect URL; raise CallbackTimeout after ``timeout`` seconds."""
    prompt_fn = prompt_fn or prompt_stderr
    result: list[str] = []
    error: list[BaseException] = []

    def ask():
        try:
            result.append(prompt_fn("Paste the full redirect URL from the browser address bar: "))
        except BaseException as exc:  # EOFError, KeyboardInterrupt in the thread
            error.append(exc)

    t = threading.Thread(target=ask, daemon=True)
    t.start()
    t.join(timeout)
    if error:
        raise ConsentError(f"no redirect URL entered ({type(error[0]).__name__})")
    if not result:
        print(file=sys.stderr)
        raise CallbackTimeout(timeout)
    return result[0]


class _LoopbackServer(HTTPServer):
    """HTTPServer without the ``socket.getfqdn()`` reverse-DNS lookup in ``server_bind``, which can stall for
    seconds on macOS before the socket starts listening."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def capture_listener(redirect_url: str, timeout: int) -> str:
    """Serve one request on 127.0.0.1 at the redirect URL's port/path; return the full callback URL."""
    target = urlparse(redirect_url)
    path = target.path or "/"
    captured: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if urlparse(self.path).path != path:
                self.send_error(404)
                return
            captured.append(f"{target.scheme}://{target.netloc}{self.path}")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_DONE_HTML)))
            self.end_headers()
            self.wfile.write(_DONE_HTML)

        def log_message(self, *args):  # keep code/state out of stderr
            pass

    deadline = time.monotonic() + timeout
    with _LoopbackServer(("127.0.0.1", target.port), Handler) as server:
        while not captured:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CallbackTimeout(timeout)
            server.timeout = min(remaining, 1.0)
            server.handle_request()
    return captured[0]
