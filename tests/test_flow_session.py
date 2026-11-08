import json
from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import (APP_ID, NOW, OTHER_IBAN, VALID_IBAN, VALID_STATUS, FakeClient, load_fixture, make_cfg,
                      make_ctx, session_record)
from enablebanking_psd2.eb.errors import ApiAuthError, ApiError, ApiNotFound
from enablebanking_psd2.eb.models import SessionStatus, TransactionsPage
from enablebanking_psd2.session_store import BankMeta
from enablebanking_psd2.errors import AccountNotFound, ConsentDenied, StateMismatch
from enablebanking_psd2.flow import ConsentRequired, Step, ensure_session, run_transactions
from enablebanking_psd2.session_store import StoredAccount, iban_hash

MAIN_IBAN, SAVINGS_IBAN = VALID_IBAN, OTHER_IBAN


def _ctx(tmp_path, client, **kwargs):
    cfg = make_cfg(tmp_path, accounts={"main": ("KBC", MAIN_IBAN), "savings": ("KBC", SAVINGS_IBAN)})
    return make_ctx(tmp_path, client, cfg, **kwargs)


def test_new_consent_stores_every_session_account(tmp_path):
    client = FakeClient()
    ctx, out, opened = _ctx(tmp_path, client)
    s = ensure_session(ctx, ctx.cfg.account("main"))
    assert not s.reused and s.uid == "acc-uid-1"
    assert opened == [load_fixture("auth.json")["url"]] and ("create_session", "CODE1") in client.calls
    (rec,) = ctx.store.records()
    assert rec.account_uid(MAIN_IBAN) == "acc-uid-1" and rec.account_uid(SAVINGS_IBAN) == "acc-uid-2"
    assert rec.bank.maximum_consent_validity == 15552000
    assert "new session" in out.getvalue() and "[✓] ACCOUNT" in out.getvalue()


def test_second_account_of_same_bank_reuses_the_session(tmp_path):
    client = FakeClient(session_status=VALID_STATUS)
    ctx, out, opened = _ctx(tmp_path, client)
    ctx.store.upsert(session_record(accounts=(StoredAccount("acc-uid-1", iban_hash(MAIN_IBAN)),
                                              StoredAccount("acc-uid-2", iban_hash(SAVINGS_IBAN)))))
    s = ensure_session(ctx, ctx.cfg.account("savings"))
    assert s.reused and s.uid == "acc-uid-2" and opened == []
    assert "reused session" in out.getvalue()


@pytest.mark.parametrize("status,exc", [
    (SessionStatus("EXPIRED", ["acc-uid-1"], datetime(2026, 11, 9, tzinfo=timezone.utc)), None),
    (SessionStatus("AUTHORIZED", ["acc-uid-1"], datetime(2026, 11, 8, 12, 2, tzinfo=timezone.utc)), None),
    (SessionStatus("AUTHORIZED", ["other"], datetime(2026, 11, 9, tzinfo=timezone.utc)), None),
    (VALID_STATUS, ApiNotFound(404, "GET", "/sessions/x")),
    (VALID_STATUS, ApiAuthError(401, "GET", "/sessions/x")),
])
def test_stale_session_triggers_new_consent(tmp_path, status, exc):
    client = FakeClient(session_status=status, session_exc=exc)
    ctx, _, opened = _ctx(tmp_path, client)
    ctx.store.upsert(session_record())
    assert not ensure_session(ctx, ctx.cfg.account("main")).reused and len(opened) == 1
    assert [r.session_id for r in ctx.store.records()] == ["99999999-8888-7777-6666-555555555555"]


def test_locally_expired_record_skips_api_check(tmp_path):
    client = FakeClient(session_status=VALID_STATUS)
    ctx, _, _ = _ctx(tmp_path, client)
    ctx.store.upsert(session_record(valid_until="2026-11-08T11:00:00+00:00"))
    ensure_session(ctx, ctx.cfg.account("main"))
    assert not any(c[0] == "get_session" for c in client.calls)


def test_fresh_skips_reuse(tmp_path):
    client = FakeClient(session_status=VALID_STATUS)
    ctx, _, opened = _ctx(tmp_path, client)
    ctx.store.upsert(session_record())
    assert not ensure_session(ctx, ctx.cfg.account("main"), fresh=True).reused and len(opened) == 1


def test_non_interactive_raises_consent_required(tmp_path):
    ctx, _, opened = _ctx(tmp_path, FakeClient())
    with pytest.raises(ConsentRequired, match="consent required for account 'main'") as exc:
        ensure_session(ctx, ctx.cfg.account("main"), interactive=False)
    assert exc.value.hint == "enablebanking authorize --account main" and opened == []
    assert ctx.step is Step.CONSENT


@pytest.mark.parametrize("consent_days,expected_days", [(None, 1), (7, 7), (365, 180), ("max", 180)])
def test_consent_days(tmp_path, consent_days, expected_days):
    client = FakeClient()
    ctx, _, _ = _ctx(tmp_path, client)
    ensure_session(ctx, ctx.cfg.account("main"), consent_days=consent_days)
    (_, _, valid_until), = [c for c in client.calls if c[0] == "start_auth"]
    assert valid_until == NOW + timedelta(days=expected_days, seconds=-60)


def test_denied_and_state_mismatch_store_nothing(tmp_path):
    client = FakeClient()
    ctx, _, _ = _ctx(tmp_path, client, url_fn=lambda: f"https://x/cb?error=access_denied&state={client.state}")
    with pytest.raises(ConsentDenied):
        ensure_session(ctx, ctx.cfg.account("main"))
    ctx, _, _ = _ctx(tmp_path, client, url_fn=lambda: "https://x/cb?code=C&state=forged")
    with pytest.raises(StateMismatch):
        ensure_session(ctx, ctx.cfg.account("main"))
    assert ctx.store.records() == [] and not any(c[0] == "create_session" for c in client.calls)


def test_account_not_in_session_still_stores_the_session(tmp_path):
    cfg = make_cfg(tmp_path, accounts={"nl": ("KBC", "NL91ABNA0417164300")})
    ctx, _, _ = make_ctx(tmp_path, FakeClient(), cfg)
    with pytest.raises(AccountNotFound) as exc:
        ensure_session(ctx, cfg.account())
    assert "BE68 **** **** 7034 Zichtrekening EUR" in exc.value.message and ctx.step is Step.ACCOUNT
    assert len(ctx.store.records()) == 1  # usable for the session's other accounts


def test_save_failure_warns_and_continues(tmp_path, monkeypatch):
    def broken(self, record):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("enablebanking_psd2.session_store.SessionStore.upsert", broken)
    ctx, out, _ = _ctx(tmp_path, FakeClient())
    assert ensure_session(ctx, ctx.cfg.account("main")).uid == "acc-uid-1"
    assert "session not saved (Permission denied)" in out.getvalue()


def test_consent_url_shown_in_quiet_mode(tmp_path):
    ctx, out, _ = _ctx(tmp_path, FakeClient(), quiet=True)
    ensure_session(ctx, ctx.cfg.account("main"))
    assert load_fixture("auth.json")["url"] in out.getvalue() and "[✓]" not in out.getvalue()


def test_reuse_from_migrated_v1_file(tmp_path):
    ctx, out, opened = _ctx(tmp_path, FakeClient(session_status=VALID_STATUS))
    ctx.store.path.parent.mkdir()
    v1 = {"schema_version": 1, "session_id": "old-session", "app_id": APP_ID, "bank_name": "KBC",
          "bank_country": "BE", "iban_sha256": iban_hash(MAIN_IBAN), "account_uid": "acc-uid-1",
          "valid_until": "2026-11-09T10:00:00+00:00", "created_at": "2026-11-08T10:00:00+00:00"}
    ctx.store.path.write_text(json.dumps(v1))
    assert ensure_session(ctx, ctx.cfg.account("main")).reused and opened == []
    assert json.loads(ctx.store.path.read_text())["schema_version"] == 1  # reads never write


def _session(tmp_path, client):
    ctx, out, _ = make_ctx(tmp_path, client, stored=True)
    return ctx, out, ensure_session(ctx, ctx.cfg.account())


def test_transactions(tmp_path):
    ctx, out, s = _session(tmp_path, FakeClient())
    tx = run_transactions(ctx, s, date(2026, 10, 29), date(2026, 11, 8))
    assert len(tx.txns) == 4 and tx.txns[0].effective_date == date(2026, 11, 7)
    assert str(tx.totals["EUR"].net) == "2447.51"
    assert "4 transactions 2026-10-29..2026-11-08, 2 page(s)" in out.getvalue()


def test_zero_and_truncated(tmp_path):
    ctx, out, s = _session(tmp_path, FakeClient(pages=[TransactionsPage([], "k", {}, truncated=True)]))
    tx = run_transactions(ctx, s, date(2026, 10, 29), date(2026, 11, 8))
    assert tx.txns == [] and "no transactions in range" in out.getvalue() and "truncated" in out.getvalue()


@pytest.mark.parametrize("status,error,hinted", [(400, None, True), (422, "WRONG_DATE", True),
                                                 (422, "PSU_HEADER_NOT_PROVIDED", False), (500, None, False)])
def test_range_hint(tmp_path, status, error, hinted):
    ctx, _, s = _session(tmp_path, FakeClient(tx_exc=ApiError(status, "GET", "/accounts/u/transactions",
                                                               error=error)))
    with pytest.raises(ApiError) as exc:
        run_transactions(ctx, s, date(2026, 1, 1), date(2026, 11, 8))
    assert ("90 days" in (exc.value.hint or "")) is hinted


def test_reuse_with_bank_metadata_skips_the_bank_list(tmp_path):
    client = FakeClient(session_status=VALID_STATUS)
    ctx, out, _ = _ctx(tmp_path, client)
    ctx.store.upsert(session_record(bank=BankMeta(15552000, ("Psu-User-Agent",))))
    assert ensure_session(ctx, ctx.cfg.account("main")).reused
    assert not any(c[0] == "aspsps" for c in client.calls)
    assert "from the stored session" in out.getvalue() and client.psu_headers == {"Psu-User-Agent": "test"}


def test_v1_record_without_bank_metadata_looks_the_bank_up(tmp_path):
    client = FakeClient(session_status=VALID_STATUS)
    ctx, _, _ = _ctx(tmp_path, client)
    ctx.store.upsert(session_record())  # bank=None, as migrated from 002
    assert ensure_session(ctx, ctx.cfg.account("main")).reused
    assert ("aspsps", "BE") in client.calls
