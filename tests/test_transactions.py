from datetime import date
from decimal import Decimal

from conftest import load_fixture
from enablebanking_psd2.transactions import normalize, sort_txns, summarize

FROM, TO = date(2026, 10, 29), date(2026, 11, 8)


def _txns(name="transactions_p1.json"):
    return [normalize(t, FROM, TO) for t in load_fixture(name)["transactions"]]


def test_sign_and_counterparty():
    debit, credit, pending = _txns()
    assert debit.amount == Decimal("-42.50")
    assert debit.counterparty_name == "Delhaize"
    assert debit.counterparty_iban == "BE71096123456769"
    assert debit.remittance == "Groceries / Card 1234"
    assert debit.reference == "REF-001"
    assert credit.amount == Decimal("2500.00")
    assert credit.counterparty_name == "Employer NV"
    assert credit.reference == "TX-002"
    assert pending.status == "PDNG"
    assert pending.booking_date is None and pending.reference is None and pending.remittance is None


def test_out_of_range_flag():
    assert not any(t.out_of_range for t in _txns())
    (old,) = _txns("transactions_p2.json")
    assert old.out_of_range


def test_sort_uses_date_fallback():
    ordered = sort_txns(_txns())
    assert [t.effective_date for t in ordered] == [date(2026, 11, 7), date(2026, 11, 5), date(2026, 11, 1)]


def test_summary_per_currency():
    s = summarize(_txns() + _txns("transactions_p2.json"))
    assert s["EUR"].count == 3
    assert s["EUR"].credits == Decimal("2500.00")
    assert s["EUR"].debits == Decimal("-52.49")
    assert s["EUR"].net == Decimal("2447.51")
    assert s["USD"].net == Decimal("-15.00")


def test_tolerates_missing_fields():
    t = normalize({}, FROM, TO)
    assert t.amount is None and t.currency is None and not t.out_of_range
    assert summarize([t]) == {}


def test_sign_without_indicator_keeps_bank_sign():
    t = normalize({"transaction_amount": {"currency": "EUR", "amount": "-25.00"},
                   "creditor": {"name": "Shop"}, "debtor": {"name": "Me"}}, FROM, TO)
    assert t.amount == Decimal("-25.00") and t.counterparty_name == "Shop"
    t = normalize({"transaction_amount": {"currency": "EUR", "amount": "25.00"}, "credit_debit_indicator": "dbit",
                   "creditor": {"name": "Shop"}}, FROM, TO)
    assert t.amount == Decimal("-25.00") and t.counterparty_name == "Shop"
    t = normalize({"transaction_amount": {"currency": "EUR", "amount": "-5"}, "credit_debit_indicator": "CRDT",
                   "debtor": {"name": "Refund Co"}}, FROM, TO)
    assert t.amount == Decimal("5") and t.counterparty_name == "Refund Co"
