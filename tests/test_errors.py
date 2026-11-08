import httpx

from conftest import load_fixture
from psd2demo.eb import errors


def _resp(status, body=None, text=None):
    req = httpx.Request("GET", "https://api.enablebanking.com/aspsps")
    if body is not None:
        return httpx.Response(status, json=body, request=req)
    return httpx.Response(status, text=text or "", request=req)


def test_auth_error():
    err = errors.from_response(_resp(401, load_fixture("error_401.json")))
    assert isinstance(err, errors.ApiAuthError)
    assert err.message == "Invalid JWT"
    assert "status=401" in err.describe() and "endpoint=GET /aspsps" in err.describe()
    assert "kid" in err.hint


def test_status_mapping():
    assert isinstance(errors.from_response(_resp(429, load_fixture("error_429.json"))), errors.ApiRateLimited)
    assert isinstance(errors.from_response(_resp(404, {})), errors.ApiNotFound)
    assert isinstance(errors.from_response(_resp(502, text="Bad gateway")), errors.ApiServerError)
    plain = errors.from_response(_resp(400, {"message": "bad"}))
    assert type(plain) is errors.ApiError and plain.hint is None


def test_gone_codes_are_not_found():
    assert isinstance(errors.from_response(_resp(400, {"error": "SESSION_NOT_FOUND"})), errors.ApiNotFound)
    assert type(errors.from_response(_resp(400, {"error": "WRONG_REQUEST"}))) is errors.ApiError


def test_known_error_hint():
    err = errors.from_response(_resp(403, {"message": "denied", "error": "ACCESS_DENIED"}))
    assert "not linked" in err.hint
    assert "error=ACCESS_DENIED" in err.describe()


def test_non_json_body():
    err = errors.from_response(_resp(500, text="<html>oops</html>"))
    assert "oops" in err.message


def test_code_shown_when_different_from_status():
    err = errors.from_response(_resp(400, {"code": 4221, "message": "bad", "error": "WRONG_REQUEST"}))
    assert err.code == 4221 and "code=4221" in err.describe()
    same = errors.from_response(_resp(401, {"code": 401, "message": "Invalid JWT"}))
    assert "code=" not in same.describe()
