from dataclasses import replace
from datetime import date, timedelta

from conftest import (BOTH_KBC, NOW, THREE_ACCOUNTS, VALID_IBAN, VALID_STATUS, FakeClient, make_cfg, make_store,
                      session_record)
from enablebanking_psd2.cache import HistoryCache
from enablebanking_psd2.eb.errors import ApiNotFound, ApiRateLimited, ApiServerError
from enablebanking_psd2.eb.models import TransactionsPage
from enablebanking_psd2.history import HistoryRequest, build_document
from enablebanking_psd2.session_store import BankMeta
from test_schema import validate

KBC = session_record(accounts=BOTH_KBC, valid_until="2027-05-07T10:00:00+00:00", bank=BankMeta(15552000, ()))
FROM, TO = date(2026, 10, 9), date(2026, 11, 8)
CFG_ACCOUNTS = THREE_ACCOUNTS


def _req(tmp_path, accounts=None, **kw):
    cfg = make_cfg(tmp_path, accounts=CFG_ACCOUNTS)
    base = HistoryRequest(accounts=tuple(cfg.select(accounts)), date_from=FROM, date_to=TO, include=frozenset(),
                          max_age=60)
    return replace(base, **kw)


def _run(tmp_path, client=None, req=None, records=(KBC,), now=NOW):
    cfg = make_cfg(tmp_path, accounts=CFG_ACCOUNTS)
    store = make_store(tmp_path)
    for rec in records:
        store.upsert(rec)
    client = client or FakeClient(session_status=VALID_STATUS)
    cache = HistoryCache(tmp_path / ".psd2demo" / "cache")
    doc = build_document(cfg, lambda: client, store, cache, req or _req(tmp_path), now=now)
    validate(doc)
    return doc, client, {a["name"]: a for a in doc["accounts"]}


def test_statuses_partial_run(tmp_path):
    doc, client, acc = _run(tmp_path)
    assert {n: a["status"] for n, a in acc.items()} == {"main": "ok", "savings": "ok", "giro": "consent_required"}
    assert doc["summary"]["accounts"] == {"ok": 2, "consent_required": 1, "rate_limited": 0, "error": 0}
    assert acc["giro"]["error"]["hint"] == "enablebanking authorize --account giro" and acc["giro"]["api_calls"] == 0
    assert acc["main"]["iban"] == VALID_IBAN and len(acc["main"]["transactions"]) == 4
    assert acc["main"]["session_valid_until"] == "2027-05-07T10:00:00+00:00" and not acc["main"]["cached"]
    assert not any(c[0] == "aspsps" for c in client.calls)  # bank rules come from the stored session
    assert acc["main"]["api_calls"] == 2  # GET /sessions + one fake transactions call


def test_store_is_never_modified(tmp_path):
    _run(tmp_path, records=(KBC,))
    path = tmp_path / ".psd2demo" / "session.json"
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    _run(tmp_path, records=())
    _run(tmp_path, FakeClient(session_status=VALID_STATUS, session_exc=ApiNotFound(404, "GET", "/sessions/x")), records=())
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_stale_or_unknown_session_is_consent_required(tmp_path):
    expired = replace(KBC, valid_until="2026-11-08T11:00:00+00:00")
    _, client, acc = _run(tmp_path, records=(expired,))
    assert acc["main"]["status"] == "consent_required" and client.calls == []  # decided locally
    _, _, acc = _run(tmp_path, FakeClient(session_status=VALID_STATUS,
                                           session_exc=ApiNotFound(404, "GET", "/sessions/x")))
    assert acc["main"]["status"] == "consent_required" and acc["main"]["error"]["step"] == "SESSION"


def test_cache_hit_miss_and_expiry(tmp_path):
    _, first, acc = _run(tmp_path)
    assert acc["main"]["api_calls"] > 0
    _, second, acc = _run(tmp_path)
    assert acc["main"]["cached"] and acc["main"]["api_calls"] == 0 and second.calls == []
    assert acc["main"]["fetched_at"] == "2026-11-08T12:00:00Z"
    _, third, acc = _run(tmp_path, now=NOW + timedelta(minutes=61))
    assert not acc["main"]["cached"] and third.calls


def test_no_cache_neither_reads_nor_writes(tmp_path):
    _run(tmp_path, req=_req(tmp_path, use_cache=False))
    assert not (tmp_path / ".psd2demo" / "cache").exists()
    _run(tmp_path)
    _, client, acc = _run(tmp_path, req=_req(tmp_path, use_cache=False))
    assert not acc["main"]["cached"] and client.calls


def test_rate_limited_with_and_without_stale_cache(tmp_path):
    limited = ApiRateLimited(429, "GET", "/accounts/u/transactions", message="Too many requests")
    _, _, acc = _run(tmp_path, FakeClient(session_status=VALID_STATUS, tx_exc=limited))
    assert acc["main"]["status"] == "rate_limited" and acc["main"]["error"]["http_status"] == 429
    assert acc["main"]["error"]["step"] == "TRANSACTIONS"
    _run(tmp_path)  # fills the cache
    doc, _, acc = _run(tmp_path, FakeClient(session_status=VALID_STATUS, tx_exc=limited),
                       now=NOW + timedelta(hours=5))
    assert acc["main"]["status"] == "ok" and acc["main"]["cached"] and acc["main"]["stale"]
    assert acc["main"]["fetched_at"] == "2026-11-08T12:00:00Z" and "rate limited" in acc["main"]["warnings"][-1]["message"]


def test_other_errors_and_isolation(tmp_path):
    class Boom(FakeClient):
        def iter_transactions(self, uid, *a, **k):
            if uid == "acc-uid-1":
                raise RuntimeError(f"unexpected for {VALID_IBAN.replace('BE68', 'BE71')}")
            return super().iter_transactions(uid, *a, **k)

    doc, _, acc = _run(tmp_path, Boom(session_status=VALID_STATUS))
    assert acc["main"]["status"] == "error" and acc["main"]["error"]["step"] == "internal"
    assert "BE71 ****" in acc["main"]["error"]["message"]  # redacted
    assert acc["savings"]["status"] == "ok"  # isolation
    _, _, acc = _run(tmp_path, FakeClient(session_status=VALID_STATUS,
                                           tx_exc=ApiServerError(503, "GET", "/accounts/u/transactions")))
    assert acc["main"]["status"] == "error" and acc["main"]["error"]["http_status"] == 503


def test_include_parts_and_part_failure(tmp_path):
    req = _req(tmp_path, ["main"], include=frozenset({"balances", "details", "raw"}))
    doc, _, acc = _run(tmp_path, req=req)
    main = acc["main"]
    assert [b["type"] for b in main["balances"]] == ["CLBD", "ITAV", "ZZZZ"] and main["details"]["product"] == "KBC Plus"
    assert all("raw" in t for t in main["transactions"]) and doc["include"] == ["balances", "details", "raw"]

    class NoDetails(FakeClient):
        def get_account_details(self, uid):
            raise ApiServerError(502, "GET", "/accounts/u/details")

    _, _, acc = _run(tmp_path, NoDetails(session_status=VALID_STATUS),
                     req=_req(tmp_path, ["main"], include=frozenset({"details"}), use_cache=False))
    assert acc["main"]["status"] == "ok" and acc["main"]["details"] is None
    assert acc["main"]["warnings"] == [{"part": "details", "message": "HTTP 502", "http_status": 502}]
    _, _, acc = _run(tmp_path, req=_req(tmp_path, accounts=["main"], use_cache=False))
    assert "balances" not in acc["main"] and all("raw" not in t for t in acc["main"]["transactions"])


def test_truncated_warning(tmp_path):
    client = FakeClient(session_status=VALID_STATUS, pages=[TransactionsPage([], "k", {}, truncated=True)])
    _, _, acc = _run(tmp_path, client, req=_req(tmp_path, accounts=["main"]))
    assert acc["main"]["truncated"] and acc["main"]["warnings"][0]["part"] == "transactions"


def test_v1_record_falls_back_to_the_bank_list(tmp_path):
    v1_like = replace(KBC, bank=None)  # as migrated from 002
    _, client, acc = _run(tmp_path, records=(v1_like,), req=_req(tmp_path, accounts=["main"]))
    assert acc["main"]["status"] == "ok" and ("aspsps", "BE") in client.calls


def test_selection_order(tmp_path):
    doc, _, _ = _run(tmp_path, req=_req(tmp_path, accounts=["giro", "main"]))
    assert [a["name"] for a in doc["accounts"]] == ["giro", "main"]


def test_totals_over_ok_accounts(tmp_path):
    doc, _, _ = _run(tmp_path)
    assert doc["summary"]["totals"]["EUR"]["count"] == 6  # 3 EUR transactions x 2 ok accounts


def test_accounts_of_one_bank_check_the_session_once(tmp_path):
    _, client, acc = _run(tmp_path, req=_req(tmp_path, ["main", "savings"]))
    assert [c[0] for c in client.calls].count("get_session") == 1
    assert acc["main"]["status"] == acc["savings"]["status"] == "ok"


def test_rate_limited_part_keeps_the_transactions(tmp_path):
    class LimitedBalances(FakeClient):
        def get_balances(self, uid):
            raise ApiRateLimited(429, "GET", "/accounts/u/balances")

    _, _, acc = _run(tmp_path, LimitedBalances(session_status=VALID_STATUS),
                     req=_req(tmp_path, ["main"], include=frozenset({"balances"})))
    assert acc["main"]["status"] == "ok" and len(acc["main"]["transactions"]) == 4 and acc["main"]["balances"] is None
    assert acc["main"]["warnings"][0]["http_status"] == 429
    assert not list((tmp_path / ".psd2demo" / "cache").glob("*.json"))  # partial results aren't cached


def test_reconsent_invalidates_the_cache(tmp_path):
    _run(tmp_path, req=_req(tmp_path, ["main"]))
    _, client, acc = _run(tmp_path, req=_req(tmp_path, ["main"]), records=(replace(KBC, session_id="new-session"),))
    assert not acc["main"]["cached"] and client.calls


NL_IBAN = "NL91ABNA0417164300"


def test_account_outside_belgium_with_other_currencies(tmp_path):
    from enablebanking_psd2.session_store import StoredAccount, iban_hash
    pages = [TransactionsPage.from_json({"transactions": [
        {"entry_reference": "N1", "transaction_amount": {"currency": "USD", "amount": "12.50"},
         "credit_debit_indicator": "DBIT", "status": "BOOK", "booking_date": "2026-10-20",
         "creditor": {"name": "Coffee Co"}, "remittance_information": []},
        {"entry_reference": "N2", "transaction_amount": {"currency": "EUR", "amount": "100.00"},
         "credit_debit_indicator": "CRDT", "status": "BOOK", "booking_date": "2026-10-21",
         "debtor": {"name": "Employer"}, "remittance_information": []}], "continuation_key": None})]
    cfg = make_cfg(tmp_path, accounts={"nl": ("ING", NL_IBAN, "NL")})
    store = make_store(tmp_path)
    store.upsert(session_record(bank_name="ING", bank_country="NL", valid_until="2027-05-07T10:00:00+00:00",
                                accounts=(StoredAccount("acc-uid-1", iban_hash(NL_IBAN)),),
                                bank=BankMeta(15552000, ())))
    client = FakeClient(pages=pages, session_status=VALID_STATUS)
    req = HistoryRequest(accounts=tuple(cfg.select()), date_from=FROM, date_to=TO, include=frozenset(), max_age=60)
    doc = build_document(cfg, lambda: client, store, HistoryCache(tmp_path / ".psd2demo" / "cache"), req, now=NOW)
    validate(doc)
    entry = doc["accounts"][0]
    assert entry["status"] == "ok" and entry["country"] == "NL" and entry["iban"] == NL_IBAN
    assert doc["summary"]["totals"] == {
        "USD": {"count": 1, "credits": "+0", "debits": "-12.50", "net": "-12.50"},
        "EUR": {"count": 1, "credits": "+100.00", "debits": "+0", "net": "+100.00"}}
