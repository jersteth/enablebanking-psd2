from conftest import OTHER_IBAN, VALID_IBAN, FakeClient, cmd_args, make_ctx
from enablebanking_psd2.commands import accounts
from enablebanking_psd2.eb.models import flatten


def _run(tmp_path, *argv):
    client = FakeClient()
    ctx, _, _ = make_ctx(tmp_path, client)
    return accounts(ctx, cmd_args("accounts", *argv)), client


def test_lists_session_accounts_selected_unmasked(tmp_path):
    res, client = _run(tmp_path)
    rows = res.summary["accounts"]
    assert [r["selected"] for r in rows] == [True, False]
    assert rows[0]["iban"] == VALID_IBAN and rows[1]["iban"] == "BE71 **** **** 6769"
    assert rows[1]["name"] == "Spaarrekening" and res.summary["details"] is None
    assert res.summary["iban_masked"] == "BE68 **** **** 7034"
    assert not any(c[0] == "details" for c in client.calls)
    text = res.render()
    assert VALID_IBAN in text and "BE71 **** **** 6769" in text and "* the selected account" in text


def test_details_flattened_and_other_ibans_masked(tmp_path):
    res, client = _run(tmp_path, "--details")
    d = res.summary["details"]
    assert ("details", "acc-uid-1") in client.calls
    assert d["account_id.iban"] == VALID_IBAN and d["account_servicer.bic_fi"] == "KREDBEBB"
    assert d["all_account_ids"] == "identification=BE71 **** **** 6769, scheme_name=IBAN"
    assert OTHER_IBAN not in str(d) and "details" not in d  # null dropped
    assert "Field" in res.render() and "KBC Plus" in res.render()


def test_flatten():
    assert flatten({"a": {"b": 1, "c": None}, "l": [1, "", 2], "e": [], "s": "x"}) == {"a.b": "1", "l": "1; 2", "s": "x"}
