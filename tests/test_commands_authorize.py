from datetime import timedelta

from conftest import (BOTH_KBC, NOW, THREE_ACCOUNTS, VALID_STATUS, FakeClient, cmd_args, make_cfg, make_ctx,
                      session_record)
from enablebanking_psd2.commands import authorize
from enablebanking_psd2.eb.errors import ApiNotFound, ApiServerError
from enablebanking_psd2.eb.models import SessionStatus
from enablebanking_psd2.session_store import BankMeta


def _ctx(tmp_path, client=None):
    client = client or FakeClient(session_status=VALID_STATUS)
    ctx, out, opened = make_ctx(tmp_path, client, make_cfg(tmp_path, accounts=THREE_ACCOUNTS))
    return ctx, out, opened, client


def _statuses(res):
    return {r["account"]: r["status"] for r in res.summary["accounts"]}


def test_one_consent_per_bank_with_bank_maximum(tmp_path):
    ctx, _, opened, client = _ctx(tmp_path)
    client.session_fixtures = ["session_create.json", "session_create_belfius.json"]
    res = authorize(ctx, cmd_args("authorize"))
    assert res.ok and _statuses(res) == {"main": "authorized", "savings": "authorized", "giro": "authorized"}
    auths = [c for c in client.calls if c[0] == "start_auth"]
    assert len(auths) == 2 and len(opened) == 2
    assert auths[0][2] == NOW + timedelta(days=180, seconds=-60)  # KBC fixture maximum: 180 days
    assert sorted(r.bank_name for r in ctx.store.records()) == ["Belfius", "KBC"]
    assert "authorized" in res.render()


def test_skips_banks_with_long_valid_consent(tmp_path):
    ctx, out, opened, client = _ctx(tmp_path)
    ctx.store.upsert(session_record(accounts=BOTH_KBC, valid_until=(NOW + timedelta(days=30)).isoformat()))
    client.session_fixtures = ["session_create_belfius.json"]
    res = authorize(ctx, cmd_args("authorize"))
    assert res.ok and len(opened) == 1 and "KBC: consent valid until" in out.getvalue()


def test_renews_consent_expiring_within_a_week_or_with_fresh(tmp_path):
    ctx, _, opened, _ = _ctx(tmp_path)
    ctx.store.upsert(session_record(accounts=BOTH_KBC, valid_until=(NOW + timedelta(days=3)).isoformat()))
    assert authorize(ctx, cmd_args("authorize", "--account", "main")).ok and len(opened) == 1
    ctx.store.upsert(session_record(accounts=BOTH_KBC, valid_until=(NOW + timedelta(days=90)).isoformat()))
    assert authorize(ctx, cmd_args("authorize", "--account", "main", "--fresh")).ok and len(opened) == 2


def test_consent_days_flag(tmp_path):
    ctx, _, _, client = _ctx(tmp_path)
    authorize(ctx, cmd_args("authorize", "--account", "main", "--consent-days", "30"))
    (_, _, valid_until), = [c for c in client.calls if c[0] == "start_auth"]
    assert valid_until == NOW + timedelta(days=30, seconds=-60)


def test_account_missing_from_the_consent(tmp_path):
    ctx, out, _, client = _ctx(tmp_path)
    client.session_fixtures = ["session_create_belfius.json"]  # KBC consent comes back with a Belfius account
    res = authorize(ctx, cmd_args("authorize", "--account", "main", "--account", "savings"))
    assert not res.ok and res.exit_code == 1 and _statuses(res) == {"main": "missing", "savings": "missing"}
    assert "is not in the KBC consent" in out.getvalue()


def test_failing_bank_does_not_stop_the_others(tmp_path):
    class FailingFirstAuth(FakeClient):
        def start_auth(self, aspsp, **kw):
            if aspsp.name == "KBC":
                raise ApiServerError(500, "POST", "/auth")
            return super().start_auth(aspsp, **kw)

    ctx, _, _, client = _ctx(tmp_path, FailingFirstAuth(session_status=VALID_STATUS))
    client.session_fixtures = ["session_create_belfius.json"]
    res = authorize(ctx, cmd_args("authorize"))
    assert res.exit_code == 1 and _statuses(res) == {"main": "failed", "savings": "failed", "giro": "authorized"}


def test_status(tmp_path):
    ctx, _, opened, client = _ctx(tmp_path)
    ctx.store.upsert(session_record(accounts=BOTH_KBC, valid_until=(NOW + timedelta(days=3)).isoformat(),
                                    bank=BankMeta(15552000, ())))
    res = authorize(ctx, cmd_args("authorize", "--status"))
    assert _statuses(res) == {"main": "expiring", "savings": "expiring", "giro": "consent_required"}
    assert not res.ok and opened == [] and not any(c[0] == "start_auth" for c in client.calls)
    gone = FakeClient(session_exc=ApiNotFound(404, "GET", "/sessions/x"))
    ctx, _, _, _ = _ctx(tmp_path, gone)  # a fresh context, as each CLI command gets one
    assert _statuses(authorize(ctx, cmd_args("authorize", "--status", "--account", "main"))) == {"main": "consent_required"}
    assert [c[0] for c in client.calls].count("get_session") == 1  # main and savings share one session check


def test_status_all_authorized_is_ok(tmp_path):
    long_valid = SessionStatus("AUTHORIZED", ["acc-uid-1"], NOW + timedelta(days=90))
    ctx, _, _, client = _ctx(tmp_path, FakeClient(session_status=long_valid))
    ctx.store.upsert(session_record(valid_until=(NOW + timedelta(days=90)).isoformat()))
    res = authorize(ctx, cmd_args("authorize", "--status", "--account", "main"))
    assert res.ok and _statuses(res) == {"main": "authorized"}


def test_unknown_account(tmp_path):
    ctx, out, _, _ = _ctx(tmp_path)
    res = authorize(ctx, cmd_args("authorize", "--account", "ghost"))
    assert res.exit_code == 2 and "unknown account 'ghost'" in out.getvalue()
