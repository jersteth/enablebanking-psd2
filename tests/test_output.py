import json
import stat
from datetime import date

from conftest import load_fixture
from enablebanking_psd2.output import export_json, render_summary, render_transactions
from enablebanking_psd2.transactions import normalize, sort_txns, summarize

FROM, TO = date(2026, 10, 29), date(2026, 11, 8)


def _txns():
    raw = load_fixture("transactions_p1.json")["transactions"] + load_fixture("transactions_p2.json")["transactions"]
    return sort_txns([normalize(t, FROM, TO) for t in raw])


def test_table_golden():
    expected = (
        "Booked       Value          Amount  Cur  Counterparty  Counterparty IBAN  Remittance             Status  Ref\n"
        "-----------  ----------  ---------  ---  ------------  -----------------  ---------------------  ------  -------\n"
        "-            2026-11-07      -9.99  EUR  Streaming Co  -                  -                      PDNG    -\n"
        "2026-11-05   2026-11-05     -42.50  EUR  Delhaize      BE71096123456769   Groceries / Card 1234  BOOK    REF-001\n"
        "2026-11-01   2026-11-01  +2,500.00  EUR  Employer NV   BE62510007547061   Salary October         BOOK    TX-002\n"
        "2026-10-20*  -              -15.00  USD  US Shop       -                  -                      BOOK    REF-004\n"
        "* returned by the bank outside the requested date range"
    )
    assert render_transactions(_txns(), width=200) == expected


def test_remittance_truncated_to_width():
    out = render_transactions(_txns(), width=100)
    assert "Groceries / Card 1234" not in out and "…" in out


def test_empty():
    assert render_transactions([]) == "No transactions in range."
    assert render_summary({}) == "Summary: no transactions."


def test_summary():
    out = render_summary(summarize(_txns()))
    assert "EUR: 3 transactions, credits +2,500.00, debits -52.49, net +2,447.51" in out
    assert "USD: 1 transactions" in out


def test_export_json(tmp_path):
    path = tmp_path / "exports" / "run.json"
    export_json(path, {"bank": "KBC", "iban_masked": "BE68 **** **** 7034"}, _txns(), [{"transactions": []}])
    data = json.loads(path.read_text())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert data["meta"]["bank"] == "KBC" and "generated_at" in data["meta"]
    assert data["transactions"][1]["amount"] == "-42.50"
    assert data["transactions"][1]["booking_date"] == "2026-11-05"
    assert data["transactions"][0]["effective_date"] == "2026-11-07"
    assert data["raw_pages"] == [{"transactions": []}]
