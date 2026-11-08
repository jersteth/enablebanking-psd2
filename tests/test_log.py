import logging

from psd2demo.log import RedactingFilter, setup_logging

TARGET = "BE68539007547034"


def _redact(text):
    return RedactingFilter(TARGET).redact(text)


def test_redacts_secrets():
    assert _redact("Authorization: Bearer eyJ.abc-def_ghi") == "Authorization: Bearer ***"
    assert _redact("https://x/cb?code=SECRET&state=XYZ") == "https://x/cb?code=***&state=***"
    assert _redact('{"code": "SECRET", "x": 1}') == '{"code": "***", "x": 1}'
    assert _redact('{"code": 401}') == '{"code": 401}'


def test_masks_other_ibans_only():
    out = _redact(f"{TARGET} and BE71096123456769")
    assert TARGET in out and "BE71 **** **** 6769" in out


def test_filter_applied(capsys):
    logger = setup_logging(True, TARGET)
    logger.debug("token Bearer abc.def %s", "code=zzz")
    err = capsys.readouterr().err
    assert "abc.def" not in err and "zzz" not in err
    setup_logging(False)
    assert logger.level == logging.WARNING
