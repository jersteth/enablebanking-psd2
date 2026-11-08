import io
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from enablebanking_psd2.consent import capture_listener, capture_paste, prompt_stderr
from enablebanking_psd2.errors import CallbackTimeout, ConsentError


def test_paste_returns_input():
    assert capture_paste(5, prompt_fn=lambda _: "https://x/cb?code=1") == "https://x/cb?code=1"


def test_paste_timeout():
    released = threading.Event()
    with pytest.raises(CallbackTimeout):
        capture_paste(0.1, prompt_fn=lambda _: released.wait(5) and "late")
    released.set()


def test_paste_eof():
    def eof(_):
        raise EOFError
    with pytest.raises(ConsentError, match="EOFError"):
        capture_paste(5, prompt_fn=eof)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_listener_captures_and_ignores_wrong_path():
    port = _free_port()
    statuses = []

    def hit():
        for _ in range(100):  # wait until the server is listening
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/other")
            except urllib.error.HTTPError as e:
                statuses.append(e.code)
                break
            except urllib.error.URLError:
                time.sleep(0.01)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=c1&state=s1") as r:
            body = r.read()
            statuses.append(r.status)
            assert b"c1" not in body and b"close this tab" in body

    t = threading.Thread(target=hit)
    t.start()
    url = capture_listener(f"http://localhost:{port}/callback", timeout=5)
    t.join()
    assert url == f"http://localhost:{port}/callback?code=c1&state=s1"
    assert statuses == [404, 200]


def test_listener_timeout():
    with pytest.raises(CallbackTimeout):
        capture_listener(f"http://localhost:{_free_port()}/callback", timeout=0.2)


def test_prompt_goes_to_stderr(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("https://x/cb?code=1\n"))
    assert prompt_stderr("Paste: ") == "https://x/cb?code=1"
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "Paste: "
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with pytest.raises(EOFError):
        prompt_stderr("Paste: ")
