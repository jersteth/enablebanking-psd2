from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from conftest import VALID_IBAN, load_fixture
from psd2demo import contract
from psd2demo.config import AccountSpec
from psd2demo.eb.models import Balance
from psd2demo.transactions import normalize

ACC = AccountSpec("main", "KBC", "BE", VALID_IBAN)
FROM, TO = date(2026, 10, 9), date(2026, 11, 8)


def _txns(*names):
    raws = [t for n in names or ("transactions_p1.json", "transactions_p2.json")
            for t in load_fixture(n)["transactions"]]
    return [normalize(r, FROM, TO) for r in raws], raws


def test_value_formatting():
    assert contract.amount(Decimal("-42.5")) == "-42.5" and contract.amount(Decimal("2500.00")) == "+2500.00"
    assert contract.amount(Decimal("1E+2")) == "+100" and contract.amount(None) is None
    assert contract.day(date(2026, 1, 2)) == "2026-01-02" and contract.day(None) is None
    plus2 = timezone(timedelta(hours=2))
    assert contract.timestamp(datetime(2026, 11, 8, 14, 0, tzinfo=plus2)) == "2026-11-08T12:00:00Z"
    assert contract.timestamp(datetime(2026, 11, 8, 12, 0)) == "2026-11-08T12:00:00Z"


def test_transaction_keys_and_order():
    txns, raws = _txns()
    out = contract.transactions(ACC, txns)
    assert list(out[0]) == ["id", "booking_date", "value_date", "transaction_date", "amount", "currency",
                            "counterparty_name", "counterparty_iban", "remittance", "status", "reference",
                            "out_of_range"]
    assert [t["value_date"] or t["booking_date"] for t in out][:3] == ["2026-11-07", "2026-11-05", "2026-11-01"]
    pending = out[0]
    assert pending["booking_date"] is None and pending["reference"] is None and pending["amount"] == "-9.99"
    assert "raw" not in pending
    with_raw = contract.transactions(ACC, txns, raws)
    assert with_raw[1]["raw"]["entry_reference"] == "REF-001"


def test_stable_ids():
    txns, _ = _txns()
    ids = {t["remittance"]: t["id"] for t in contract.transactions(ACC, txns)}
    assert ids["Groceries / Card 1234"] == "ref:REF-001" and ids["Salary October"] == "ref:TX-002"
    hashed = [t["id"] for t in contract.transactions(ACC, txns) if t["reference"] is None][0]
    assert hashed.startswith("h:") and len(hashed) == 34
    assert contract.transactions(ACC, txns)[0]["id"] == hashed  # deterministic across runs


def test_identical_transactions_get_distinct_ids_independent_of_sorting():
    coffee = {"transaction_amount": {"currency": "EUR", "amount": "3.50"}, "credit_debit_indicator": "DBIT",
              "booking_date": "2026-11-05", "creditor": {"name": "Cafe"}}
    other = {**coffee, "booking_date": "2026-11-06", "creditor": {"name": "Bakery"}}
    txns = [normalize(r, FROM, TO) for r in (coffee, other, dict(coffee))]
    ids = [t["id"] for t in contract.transactions(ACC, txns)]
    assert len(set(ids)) == 3
    reordered = [txns[1], txns[0], txns[2]]
    assert sorted(t["id"] for t in contract.transactions(ACC, reordered)) == sorted(ids)
    other_account = AccountSpec("x", "KBC", "BE", "BE71096123456769")
    assert set(t["id"] for t in contract.transactions(other_account, txns)).isdisjoint(ids)


def test_balance_and_entry_keys():
    b = contract.balance(Balance.from_json(load_fixture("balances.json")["balances"][0]))
    assert b == {"name": "Booked balance", "type": "CLBD", "type_label": "closing booked", "amount": "+1234.56",
                 "currency": "EUR", "reference_date": "2026-11-07", "last_change": "2026-11-07T23:59:59Z"}
    entry = contract.account_entry(account=ACC, bank="KBC", status="ok", transactions=[], balances=[b],
                                   include=frozenset({"balances"}))
    assert list(entry) == ["name", "bank", "country", "iban", "currency", "status", "error", "warnings",
                           "session_valid_until", "fetched_at", "truncated", "cached", "stale", "api_calls",
                           "transactions", "balances"]
    assert entry["currency"] == "EUR" and "details" not in entry


def test_document_summary_counts_ok_entries_only():
    txns, _ = _txns()
    ok = contract.account_entry(account=ACC, bank="KBC", status="ok", transactions=contract.transactions(ACC, txns))
    bad = contract.account_entry(account=AccountSpec("s", "KBC", "BE", "BE71096123456769"), bank="KBC",
                                 status="consent_required", error=contract.error("SESSION", "consent required"))
    doc = contract.document(generated_at=datetime(2026, 11, 8, 12, tzinfo=timezone.utc), tool_version="0.1.0",
                            date_from=FROM, date_to=TO, include=frozenset({"raw", "balances"}), entries=[ok, bad])
    assert list(doc) == ["schema_version", "generated_at", "tool", "range", "include", "accounts", "summary"]
    assert doc["include"] == ["balances", "raw"] and doc["schema_version"] == "1"
    assert doc["summary"]["accounts"] == {"ok": 1, "consent_required": 1, "rate_limited": 0, "error": 0}
    assert doc["summary"]["totals"] == {
        "EUR": {"count": 3, "credits": "+2500.00", "debits": "-52.49", "net": "+2447.51"},
        "USD": {"count": 1, "credits": "+0", "debits": "-15.00", "net": "-15.00"}}
