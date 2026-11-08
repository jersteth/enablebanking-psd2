import io
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from conftest import FakeClient, load_fixture, make_cfg, make_store, stored_session
from psd2demo.eb.errors import ApiNotFound
from psd2demo.eb.models import SessionStatus, TransactionsPage
from psd2demo.flow import ConsentHooks, Step, StepReporter, run_demo

NOW = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
TODAY = date(2026, 11, 8)
VALID_STATUS = SessionStatus.from_json(load_fixture("session_get.json"))


def _run(tmp_path, client, *, fresh=False, url_fn=None, iban=None):
    out = io.StringIO()
    opened = []
    url_fn = url_fn or (lambda: f"https://localhost/callback?code=CODE1&state={client.state}")
    hooks = ConsentHooks(capture=lambda cfg: url_fn(), open_browser=opened.append)
    cfg = make_cfg(tmp_path, iban=iban) if iban else make_cfg(tmp_path)
    store = make_store(tmp_path)
    res = run_demo(cfg, client, StepReporter(out), fresh=fresh, store=store, hooks=hooks, today=TODAY, now=NOW)
    return res, out.getvalue(), store, opened


def test_new_consent_full_flow(tmp_path):
    client = FakeClient()
    res, out, store, opened = _run(tmp_path, client)
    assert res.ok and res.exit_code == 0
    assert opened == [load_fixture("auth.json")["url"]]
    assert ("create_session", "CODE1") in client.calls
    assert ("transactions", "acc-uid-1", date(2026, 10, 29), TODAY) in client.calls
    assert store.load().account_uid == "acc-uid-1"
    assert len(res.txns) == 4 and res.txns[0].effective_date == date(2026, 11, 7)
    assert res.totals["EUR"].net == Decimal("2447.51")
    assert res.summary["totals"]["EUR"]["net"] == Decimal("2447.51")
    assert "new session" in out and "4 transactions" in out and "2 page(s)" in out


def test_reuse_valid_session(tmp_path):
    client = FakeClient(session_status=VALID_STATUS)
    make_store(tmp_path).save(stored_session())
    res, out, _, opened = _run(tmp_path, client)
    assert res.ok and opened == []
    assert not any(c[0] == "start_auth" for c in client.calls)
    assert "reused session" in out


@pytest.mark.parametrize("status,exc", [
    (SessionStatus("EXPIRED", ["acc-uid-1"], datetime(2026, 11, 9, tzinfo=timezone.utc)), None),
    (SessionStatus("AUTHORIZED", ["acc-uid-1"], datetime(2026, 11, 8, 12, 2, tzinfo=timezone.utc)), None),
    (SessionStatus("AUTHORIZED", ["other"], datetime(2026, 11, 9, tzinfo=timezone.utc)), None),
    (VALID_STATUS, ApiNotFound(404, "GET", "/sessions/x")),
])
def test_stale_session_triggers_new_consent(tmp_path, status, exc):
    client = FakeClient(session_status=status, exc=exc)
    make_store(tmp_path).save(stored_session())
    res, out, store, opened = _run(tmp_path, client)
    assert res.ok and len(opened) == 1
    assert store.load().session_id != "old-session"


def test_fresh_skips_reuse(tmp_path):
    client = FakeClient(session_status=VALID_STATUS)
    make_store(tmp_path).save(stored_session())
    res, _, _, opened = _run(tmp_path, client, fresh=True)
    assert res.ok and len(opened) == 1
    assert not any(c[0] == "get_session" for c in client.calls)


def test_denied_writes_no_session(tmp_path):
    client = FakeClient()
    res, out, store, _ = _run(tmp_path, client,
                              url_fn=lambda: f"https://localhost/callback?error=access_denied&state={client.state}")
    assert not res.ok and res.failed_step is Step.CONSENT and res.exit_code == 1
    assert store.load() is None and "access_denied" in out


def test_state_mismatch_writes_no_session(tmp_path):
    client = FakeClient()
    res, out, store, _ = _run(tmp_path, client, url_fn=lambda: "https://localhost/callback?code=C&state=forged")
    assert res.failed_step is Step.CONSENT and store.load() is None
    assert not any(c[0] == "create_session" for c in client.calls)


def test_account_not_found(tmp_path):
    res, out, store, _ = _run(tmp_path, FakeClient(), iban="NL91ABNA0417164300")
    assert res.failed_step is Step.ACCOUNT and res.exit_code == 1
    assert "BE68 **** **** 7034 Zichtrekening EUR" in out and "control panel" in out
    assert "BE68539007547034" not in out
    assert store.load() is None


def test_zero_transactions_is_pass(tmp_path):
    res, out, _, _ = _run(tmp_path, FakeClient(pages=[TransactionsPage([], None, {})]))
    assert res.ok and res.txns == [] and "no transactions in range" in out


def test_truncated_warning(tmp_path):
    page = TransactionsPage([], "k", {}, truncated=True)
    res, out, _, _ = _run(tmp_path, FakeClient(pages=[page]))
    assert res.ok and "truncated" in out
