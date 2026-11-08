import pytest

from conftest import load_fixture
from psd2demo.eb.models import Aspsp, AuthStart, Session, SessionStatus, TransactionsPage


def test_aspsp():
    a = Aspsp.from_json(load_fixture("aspsps.json")["aspsps"][0])
    assert (a.name, a.country, a.maximum_consent_validity) == ("KBC", "BE", 15552000)
    assert Aspsp.from_json(load_fixture("aspsps.json")["aspsps"][3]).maximum_consent_validity is None


def test_auth_start():
    assert AuthStart.from_json(load_fixture("auth.json")).url.startswith("https://")
    with pytest.raises(ValueError):
        AuthStart.from_json({})


def test_session():
    s = Session.from_json(load_fixture("session_create.json"))
    assert s.session_id.startswith("9999")
    assert [a.iban for a in s.accounts] == ["BE68539007547034", "BE71096123456769"]
    assert s.valid_until.year == 2026 and s.valid_until.tzinfo is not None
    with pytest.raises(ValueError):
        Session.from_json({"accounts": []})


def test_session_status():
    st = SessionStatus.from_json(load_fixture("session_get.json"))
    assert st.status == "AUTHORIZED"
    assert st.account_uids == ["acc-uid-1", "acc-uid-2"]
    assert st.valid_until.tzinfo is not None
    assert SessionStatus.from_json({"status": "EXPIRED", "accounts": [{"uid": "u"}]}).account_uids == ["u"]


def test_transactions_page():
    p = TransactionsPage.from_json(load_fixture("transactions_p1.json"))
    assert len(p.transactions) == 3 and p.continuation_key == "page2"
    assert TransactionsPage.from_json(load_fixture("transactions_p2.json")).continuation_key is None
    with pytest.raises(ValueError):
        TransactionsPage.from_json({"transactions": "nope"})


def test_aspsp_required_psu_headers():
    banks = [Aspsp.from_json(a) for a in load_fixture("aspsps.json")["aspsps"]]
    assert banks[0].required_psu_headers == []
    assert banks[2].required_psu_headers == ["Psu-Ip-Address", "Psu-User-Agent"]
