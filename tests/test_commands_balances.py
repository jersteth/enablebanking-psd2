import json

from conftest import FakeClient, cmd_args, make_ctx
from enablebanking_psd2.commands import balances
from enablebanking_psd2.output import render_balances


def _run(tmp_path, client=None, *argv):
    client = client or FakeClient()
    ctx, out, _ = make_ctx(tmp_path, client, stored=True)
    return balances(ctx, cmd_args("balances", *argv)), out, client


def test_balances(tmp_path):
    res, out, client = _run(tmp_path)
    assert res.ok and ("balances", "acc-uid-1") in client.calls and "3 balance(s)" in out.getvalue()
    first = res.summary["balances"][0]
    assert first["type"] == "CLBD" and first["type_label"] == "closing booked" and str(first["amount"]) == "1234.56"
    assert res.summary["balances"][2]["type_label"] is None


def test_zero_balances_is_pass(tmp_path):
    res, out, _ = _run(tmp_path, FakeClient(balances=[]))
    assert res.ok and "no balances" in out.getvalue() and res.render() == "The bank returned no balances."


def test_render_labels_and_unknown_code():
    text = render_balances(FakeClient().balances, width=200)
    assert "closing booked" in text and "interim available" in text and "+1,234.56" in text
    unknown = [line for line in text.splitlines() if line.startswith("ZZZZ")][0]
    assert "-5.00" in unknown and " - " in unknown


def test_export(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res, _, _ = _run(tmp_path, None, "--export", str(tmp_path / "exports" / "b.json"))
    data = json.loads((tmp_path / "exports" / "b.json").read_text())
    assert res.ok and data["meta"]["account"] == "main" and data["balances"][0]["balance_type"] == "CLBD"
