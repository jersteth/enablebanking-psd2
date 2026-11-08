import asyncio
import json
import sys
from datetime import date

import pytest

from enablebanking_psd2 import api, cli, mcp_server
from enablebanking_psd2.errors import ConfigError

mcp = pytest.importorskip("mcp")

DOC = {"schema_version": "1", "accounts": [], "summary": {"accounts": {"ok": 0}, "totals": {}}}
STATUS = {"accounts": [{"account": "main", "bank": "KBC", "iban_masked": "BE68 **** **** 7034",
                        "status": "authorized", "valid_until": "2099-01-01T00:00:00+00:00"}]}


@pytest.fixture
def fake_api(monkeypatch):
    calls = {}

    def fetch(config_path=None, **kw):
        calls["fetch"] = (config_path, kw)
        return DOC

    def status(config_path=None, **kw):
        calls["status"] = config_path
        return STATUS

    monkeypatch.setattr(api, "fetch_history", fetch)
    monkeypatch.setattr(api, "consent_status", status)
    return calls


def test_get_history_parses_dates_and_passes_arguments(fake_api, tmp_path):
    out = mcp_server.get_history(["main"], None, "2026-10-01", "2026-10-31", ["balances"], config_path=tmp_path / "c.toml")
    assert out is DOC
    path, kw = fake_api["fetch"]
    assert path == tmp_path / "c.toml"
    assert kw == {"accounts": ["main"], "days": None, "date_from": date(2026, 10, 1), "date_to": date(2026, 10, 31),
                  "include": ["balances"]}
    mcp_server.get_history()
    assert fake_api["fetch"][1] == {"accounts": None, "days": None, "date_from": None, "date_to": None, "include": None}


def test_consent_status_calls_the_api(fake_api):
    assert mcp_server.consent_status() is STATUS


def test_errors_become_value_errors(monkeypatch):
    def bad_config(*a, **k):
        raise ConfigError(["unknown account 'x'"])

    monkeypatch.setattr(api, "fetch_history", bad_config)
    with pytest.raises(ValueError, match="unknown account 'x'"):
        mcp_server.get_history(["x"])
    with pytest.raises(ValueError, match="date_from must be an ISO date"):
        mcp_server.get_history(date_from="yesterday")

    def boom(*a, **k):
        raise RuntimeError("failed for BE68539007547034")

    monkeypatch.setattr(api, "consent_status", boom)
    with pytest.raises(ValueError) as exc:
        mcp_server.consent_status()
    assert "RuntimeError" in str(exc.value) and "BE68539007547034" not in str(exc.value)  # redacted


def test_server_registers_exactly_two_read_only_tools():
    server = mcp_server.build_server()
    tools = asyncio.run(server.list_tools())
    assert sorted(t.name for t in tools) == ["consent_status", "get_history"]
    by_name = {t.name: t for t in tools}
    schema = getattr(by_name["get_history"], "input_schema", None) or by_name["get_history"].inputSchema
    assert set(schema["properties"]) == {"accounts", "days", "date_from", "date_to", "include"}
    for tool in tools:
        ann = tool.annotations
        assert (getattr(ann, "read_only_hint", None) or getattr(ann, "readOnlyHint", None)) is True
        assert "4 unattended requests" in tool.description or tool.name == "consent_status"


def test_round_trip_over_the_in_memory_transport(fake_api, capsys):
    server = mcp_server.build_server()

    async def go():
        async with mcp.Client(server) as client:
            history = await client.call_tool("get_history", {"days": 7, "include": ["balances"]})
            status = await client.call_tool("consent_status", {})
            return history, status

    history, status = asyncio.run(go())
    assert not history.is_error and not status.is_error
    assert json.loads(history.content[0].text)["schema_version"] == "1"
    assert json.loads(status.content[0].text)["accounts"][0]["status"] == "authorized"
    assert fake_api["fetch"][1]["days"] == 7 and fake_api["fetch"][1]["include"] == ["balances"]
    assert capsys.readouterr().out == ""  # stdout is reserved for the protocol


def test_a_tool_error_is_reported_as_an_error_result(monkeypatch):
    monkeypatch.setattr(api, "fetch_history", lambda *a, **k: (_ for _ in ()).throw(ConfigError(["no config"])))
    server = mcp_server.build_server()

    async def go():
        async with mcp.Client(server) as client:
            return await client.call_tool("get_history", {})

    result = asyncio.run(go())
    assert result.is_error and "no config" in result.content[0].text


def test_missing_sdk_exits_2_with_the_install_hint(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "mcp", None)  # makes `import mcp...` raise ImportError
    monkeypatch.setitem(sys.modules, "mcp.server", None)
    monkeypatch.setitem(sys.modules, "mcp.types", None)
    assert mcp_server.main() == 2
    assert "pip install 'enablebanking-psd2[mcp]'" in capsys.readouterr().err


def test_cli_mcp_subcommand(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(mcp_server, "main", lambda config, verbose: seen.append((config, verbose)) or 0)
    assert cli.main(["mcp", "--config", str(tmp_path / "c.toml"), "--verbose"]) == 0
    assert seen == [(tmp_path / "c.toml", True)]
