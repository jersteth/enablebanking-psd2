import importlib.util
import json
import py_compile
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import VALID_IBAN, VALID_STATUS, FakeClient, NOW, session_record, write_config
from enablebanking_psd2 import api
from enablebanking_psd2.session_store import SessionStore

EXAMPLES = Path(__file__).parent.parent / "examples"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"example_{name}", EXAMPLES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["fetch_history", "agent_sdk_agent"])
def test_examples_compile(name, tmp_path):
    py_compile.compile(str(EXAMPLES / f"{name}.py"), cfile=str(tmp_path / "x.pyc"), doraise=True)


def test_fetch_history_example_runs_against_a_fake_client(tmp_path, key_file, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_config(tmp_path, key_file, {"main": ("KBC", VALID_IBAN)})
    SessionStore(tmp_path / ".psd2demo" / "session.json").upsert(session_record(valid_until="2099-01-01T00:00:00+00:00"))
    example = _load("fetch_history")
    client = FakeClient(session_status=VALID_STATUS)
    real = api.fetch_history
    monkeypatch.setattr(example, "fetch_history", lambda **kw: real(env={}, client=client, today=NOW.date(), now=NOW, **kw))
    example.main()
    out = capsys.readouterr().out
    assert "spent" in out and "EUR" in out
    doc = real(env={}, client=client, today=NOW.date(), now=NOW)
    assert example.spending(doc)["EUR"] > Decimal(0)


def test_spending_ignores_credits_out_of_range_and_failed_accounts():
    example = _load("fetch_history")
    tx = lambda amount, cur="EUR", oor=False: {"amount": amount, "currency": cur, "out_of_range": oor}
    doc = {"accounts": [{"status": "ok", "transactions": [tx("-10.50"), tx("+99.00"), tx("-1.00", oor=True),
                                                           tx("-2.00", "USD")]},
                        {"status": "error", "transactions": [tx("-500.00")]}]}
    assert example.spending(doc) == {"EUR": Decimal("10.50"), "USD": Decimal("2.00")}


def test_mcp_config_snippet_is_valid_and_uses_the_extra():
    cfg = json.loads((EXAMPLES / "mcp.json").read_text())["mcpServers"]["enablebanking"]
    assert cfg["command"] == "uvx" and "enablebanking-psd2[mcp]" in cfg["args"] and cfg["args"][-2:] == ["enablebanking", "mcp"]


def test_agent_example_options_are_read_only():
    sdk = pytest.importorskip("claude_agent_sdk")
    opts = _load("agent_sdk_agent").options()
    assert opts.allowed_tools == ["Bash(enablebanking history *)", "Read"]
    assert opts.skills == ["enablebanking-psd2:bank-account-history"]
