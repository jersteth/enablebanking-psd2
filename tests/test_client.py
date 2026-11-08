import json
from datetime import date, datetime, timezone

import httpx
import pytest
import respx

from conftest import APP_ID, BASE, load_fixture
from enablebanking_psd2.eb import errors
from enablebanking_psd2.eb.auth import JwtSigner
from enablebanking_psd2.eb.client import EnableBankingClient
from enablebanking_psd2.eb.models import Aspsp

@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def client(pem_bytes, sleeps):
    signer = JwtSigner(APP_ID, pem_bytes)
    with EnableBankingClient(signer, psu_headers={"Psu-User-Agent": "test"}, sleep=sleeps.append) as c:
        yield c


@respx.mock
def test_get_aspsps(client):
    route = respx.get(f"{BASE}/aspsps").respond(json=load_fixture("aspsps.json"))
    banks = client.get_aspsps("BE")
    assert [b.name for b in banks][:2] == ["KBC", "KBC Brussels"]
    req = route.calls.last.request
    assert req.url.params["country"] == "BE" and req.url.params["psu_type"] == "personal"
    assert req.headers["Authorization"].startswith("Bearer ey")
    assert "Psu-User-Agent" not in req.headers


@respx.mock
def test_start_auth_body(client):
    route = respx.post(f"{BASE}/auth").respond(json=load_fixture("auth.json"))
    res = client.start_auth(Aspsp("KBC", "BE"), valid_until=datetime(2026, 11, 9, 10, tzinfo=timezone.utc),
                            state="st", redirect_url="https://localhost/callback")
    assert res.authorization_id
    req = route.calls.last.request
    assert json.loads(req.content) == {
        "access": {"valid_until": "2026-11-09T10:00:00Z"}, "aspsp": {"name": "KBC", "country": "BE"},
        "state": "st", "redirect_url": "https://localhost/callback", "psu_type": "personal"}
    assert req.headers["Psu-User-Agent"] == "test"


@respx.mock
def test_create_and_get_session(client):
    post = respx.post(f"{BASE}/sessions").respond(json=load_fixture("session_create.json"))
    s = client.create_session("the-code")
    assert json.loads(post.calls.last.request.content) == {"code": "the-code"}
    respx.get(f"{BASE}/sessions/{s.session_id}").respond(json=load_fixture("session_get.json"))
    assert client.get_session(s.session_id).status == "AUTHORIZED"


@respx.mock
def test_error_mapping(client):
    respx.get(f"{BASE}/aspsps").respond(401, json=load_fixture("error_401.json"))
    with pytest.raises(errors.ApiAuthError):
        client.get_aspsps("BE")


@respx.mock
def test_get_retries_5xx_then_succeeds(client, sleeps):
    respx.get(f"{BASE}/aspsps").mock(side_effect=[httpx.Response(503), httpx.Response(503),
                                                   httpx.Response(200, json={"aspsps": []})])
    assert client.get_aspsps("BE") == []
    assert sleeps == [1.0, 2.0]


@respx.mock
def test_get_gives_up_after_three_retries(client, sleeps):
    respx.get(f"{BASE}/aspsps").respond(503)
    with pytest.raises(errors.ApiServerError):
        client.get_aspsps("BE")
    assert sleeps == [1.0, 2.0, 4.0]


@respx.mock
def test_post_not_retried_on_5xx(client, sleeps):
    route = respx.post(f"{BASE}/sessions").respond(500)
    with pytest.raises(errors.ApiServerError):
        client.create_session("c")
    assert route.call_count == 1 and sleeps == []


@respx.mock
def test_post_retried_on_429_with_retry_after(client, sleeps):
    respx.post(f"{BASE}/sessions").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "120"}),
        httpx.Response(200, json=load_fixture("session_create.json"))])
    client.create_session("c")
    assert sleeps == [30.0]


@respx.mock
def test_transport_error_retried_for_get(client, sleeps):
    respx.get(f"{BASE}/aspsps").mock(side_effect=[httpx.ConnectError("boom"),
                                                   httpx.Response(200, json={"aspsps": []})])
    assert client.get_aspsps("BE") == []
    respx.get(f"{BASE}/aspsps").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(errors.ApiNetworkError, match="network error") as exc:
        client.get_aspsps("BE")
    assert "status=" not in exc.value.describe()


@respx.mock
def test_schema_error_names_real_endpoint(client):
    respx.post(f"{BASE}/sessions").respond(json={"accounts": []})
    with pytest.raises(errors.ApiSchemaError, match="session_id") as exc:
        client.create_session("c")
    assert "endpoint=POST /sessions" in exc.value.describe()


@respx.mock
def test_pagination(client):
    route = respx.get(f"{BASE}/accounts/u1/transactions").mock(side_effect=[
        httpx.Response(200, json=load_fixture("transactions_p1.json")),
        httpx.Response(200, json=load_fixture("transactions_p2.json"))])
    pages = list(client.iter_transactions("u1", date(2026, 10, 29), date(2026, 11, 8)))
    assert [len(p.transactions) for p in pages] == [3, 1]
    first, second = (c.request.url.params for c in route.calls)
    assert first["date_from"] == "2026-10-29" and first["date_to"] == "2026-11-08"
    assert "continuation_key" not in first and second["continuation_key"] == "page2"
    assert route.calls.last.request.headers["Psu-User-Agent"] == "test"


@respx.mock
def test_pagination_repeated_key_stops(client):
    route = respx.get(f"{BASE}/accounts/u1/transactions").respond(
        json={"transactions": [{}], "continuation_key": "same"})
    pages = list(client.iter_transactions("u1", date(2026, 1, 1), date(2026, 1, 2)))
    assert len(pages) == 2 and route.call_count == 2 and pages[-1].truncated


@respx.mock
def test_pagination_max_pages(client):
    keys = iter(range(100))
    respx.get(f"{BASE}/accounts/u1/transactions").mock(
        side_effect=lambda req: httpx.Response(200, json={"transactions": [], "continuation_key": f"k{next(keys)}"}))
    pages = list(client.iter_transactions("u1", date(2026, 1, 1), date(2026, 1, 2), max_pages=3))
    assert len(pages) == 3 and pages[-1].truncated


@respx.mock
def test_balances_details_and_counter(client):
    bal = respx.get(f"{BASE}/accounts/u1/balances").respond(json=load_fixture("balances.json"))
    respx.get(f"{BASE}/accounts/u1/details").respond(json=load_fixture("details.json"))
    before = client.request_count
    assert len(client.get_balances("u1")) == 3
    assert client.get_account_details("u1").raw["currency"] == "EUR"
    assert client.request_count == before + 2
    assert bal.calls.last.request.headers["Psu-User-Agent"] == "test"


@respx.mock
def test_delete_session_empty_body_and_no_5xx_retry(client, sleeps):
    ok = respx.delete(f"{BASE}/sessions/s1").respond(204)
    assert client.delete_session("s1") is None and ok.call_count == 1
    bad = respx.delete(f"{BASE}/sessions/s2").respond(503)
    with pytest.raises(errors.ApiServerError):
        client.delete_session("s2")
    assert bad.call_count == 1 and sleeps == []


@respx.mock
def test_get_session_sends_no_psu_headers(client):
    route = respx.get(f"{BASE}/sessions/s1").respond(json=load_fixture("session_get.json"))
    client.get_session("s1")
    assert "Psu-User-Agent" not in route.calls.last.request.headers
