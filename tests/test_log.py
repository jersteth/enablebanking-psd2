import logging
from datetime import timedelta

import httpx

from psd2demo.log import RedactingFilter, http_event_hooks, set_own_ibans, setup_logging, truncate_body

TARGET = "BE68539007547034"


def _redact(text):
    return RedactingFilter(frozenset({TARGET})).redact(text)


def test_redacts_secrets():
    assert _redact("Authorization: Bearer eyJ.abc-def_ghi") == "Authorization: Bearer ***"
    assert _redact("https://x/cb?code=SECRET&state=XYZ") == "https://x/cb?code=***&state=***"
    assert _redact('{"code": "SECRET", "x": 1}') == '{"code": "***", "x": 1}'
    assert _redact('{"code": 401}') == '{"code": 401}'


def test_masks_other_ibans_only():
    out = _redact(f"{TARGET} and BE71096123456769")
    assert TARGET in out and "BE71 **** **** 6769" in out


def test_filter_applied(capsys):
    logger = setup_logging(True)
    set_own_ibans([TARGET])
    logger.debug("token Bearer abc.def %s", "code=zzz")
    err = capsys.readouterr().err
    assert "abc.def" not in err and "zzz" not in err
    setup_logging(False)
    assert logger.level == logging.WARNING


def test_truncate_never_splits_an_iban():
    body = "x" * 2040 + " BE71096123456769 tail"
    out = truncate_body(body)
    assert out.endswith(" …") and "BE7109" not in out
    assert truncate_body("short") == "short"


def test_verbose_http_hook_logs_redacted_response(capsys):
    setup_logging(True)
    set_own_ibans([TARGET])
    body = '{"code": "SECRET", "accounts": ["' + TARGET + '", "BE71096123456769"], "pad": "' + "x" * 3000 + '"}'
    req = httpx.Request("GET", "https://api.enablebanking.com/sessions/s1?state=XYZ")
    resp = httpx.Response(200, text=body, request=req)
    resp.elapsed = timedelta(milliseconds=12)
    http_event_hooks()["response"][0](resp)
    err = capsys.readouterr().err
    assert "GET https://api.enablebanking.com/sessions/s1?state=*** -> 200 (12 ms)" in err
    assert "SECRET" not in err and TARGET in err and "BE71 **** **** 6769" in err
    assert err.rstrip().endswith("…") and len(err) < 2300
    setup_logging(False)
    set_own_ibans([])
