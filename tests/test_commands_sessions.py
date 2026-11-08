import io

import httpx

import pytest

from conftest import FOREIGN_IBAN, VALID_IBAN, VALID_STATUS, FakeClient, cmd_args, make_cfg, make_ctx, session_record
from psd2demo.commands import sessions
from psd2demo.eb.errors import ApiNotFound, ApiServerError, from_response
from psd2demo.flow import Step

CFG_ACCOUNTS = {"main": ("KBC", VALID_IBAN), "giro": ("Belfius", FOREIGN_IBAN)}


def _ctx(tmp_path, client=None, records=None):
    client = client or FakeClient(session_status=VALID_STATUS)
    ctx, out, _ = make_ctx(tmp_path, client, make_cfg(tmp_path, accounts=CFG_ACCOUNTS))
    for rec in records if records is not None else [
            session_record(),
            session_record(session_id="belfius-session", bank_name="Belfius", accounts=()),
            session_record(session_id="old-expired", bank_name="ING", valid_until="2026-01-01T00:00:00+00:00")]:
        ctx.store.upsert(rec)
    return ctx, out, client


def _args(action="list", *argv):
    return cmd_args(f"sessions {action}", *argv)


def test_list_with_live_status(tmp_path):
    ctx, _, _ = _ctx(tmp_path)
    res = sessions(ctx, _args())
    rows = {r["bank"]: r for r in res.summary["sessions"]}
    assert rows["KBC"]["configured"] == ["main BE68 **** **** 7034"] and rows["KBC"]["live_status"] == "AUTHORIZED"
    assert rows["ING"]["local_status"] == "expired" and rows["Belfius"]["configured"] == []
    assert "KBC" in res.render() and "AUTHORIZED" in res.render()


def test_list_offline_and_live_errors(tmp_path):
    ctx, _, client = _ctx(tmp_path)
    res = sessions(ctx, _args("list", "--offline"))
    assert all(r["live_status"] is None for r in res.summary["sessions"]) and client.calls == []
    client.session_exc = ApiNotFound(404, "GET", "/sessions/x")
    res = sessions(ctx, _args())
    assert {r["live_status"] for r in res.summary["sessions"]} == {"unknown (404)"}
    assert "(offline)" not in res.render()


def test_revoke_account(tmp_path):
    ctx, out, client = _ctx(tmp_path)
    res = sessions(ctx, _args("revoke", "--account", "main"))
    assert res.ok and client.calls == [("delete_session", "old-session")]
    assert res.summary["revoked"] == [{"bank": "KBC", "session": "old-sess", "result": "revoked"}]
    assert ctx.store.for_bank(ctx.cfg.app_id, "KBC", "BE").status == "revoked"


@pytest.mark.parametrize("exc,result", [(ApiNotFound(404, "DELETE", "/sessions/x"), "already closed"),
                                        (from_response(httpx.Response(400, json={"error": "ALREADY_CLOSED"},
                                                       request=httpx.Request("DELETE", "https://x/sessions/x"))),
                                         "already closed")])
def test_revoke_already_closed_is_success(tmp_path, exc, result):
    ctx, _, client = _ctx(tmp_path)
    client.delete_errors["old-session"] = exc
    res = sessions(ctx, _args("revoke", "--bank", "kbc"))
    assert res.ok and res.summary["revoked"][0]["result"] == result
    assert ctx.store.for_bank(ctx.cfg.app_id, "KBC", "BE").status == "revoked"


def test_revoke_all_isolates_failures(tmp_path):
    ctx, out, client = _ctx(tmp_path)
    client.delete_errors["old-session"] = ApiServerError(503, "DELETE", "/sessions/old-session")
    res = sessions(ctx, _args("revoke", "--all", "--yes"))
    assert not res.ok and res.exit_code == 1 and res.failed_step is Step.REVOKE
    assert [c[1] for c in client.calls] == ["old-session", "belfius-session", "old-expired"]
    statuses = {r.bank_name: r.status for r in ctx.store.records()}
    assert statuses == {"KBC": "active", "Belfius": "revoked", "ING": "revoked"}


def test_revoke_all_asks_for_confirmation(tmp_path, monkeypatch):
    ctx, out, client = _ctx(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    res = sessions(ctx, _args("revoke", "--all"))
    assert res.exit_code == 2 and client.calls == [] and "aborted" in out.getvalue()
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert sessions(ctx, _args("revoke", "--all")).exit_code == 2 and client.calls == []
    monkeypatch.setattr("sys.stdin", io.StringIO("yes\n"))
    assert sessions(ctx, _args("revoke", "--all")).ok and len(client.calls) == 3


def test_revoke_needs_a_target_and_a_match(tmp_path):
    ctx, out, _ = _ctx(tmp_path)
    assert sessions(ctx, _args("revoke")).exit_code == 2
    res = sessions(ctx, _args("revoke", "--bank", "Argenta"))
    assert res.exit_code == 1 and "no matching stored session" in out.getvalue()


def test_prune(tmp_path):
    ctx, out, client = _ctx(tmp_path)
    ctx.store.mark_revoked(ctx.store.for_bank(ctx.cfg.app_id, "Belfius", "BE"))
    res = sessions(ctx, _args("prune"))
    assert res.summary == {"removed": 2} and [r.bank_name for r in ctx.store.records()] == ["KBC"]
    assert client.calls == []
