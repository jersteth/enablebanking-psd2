import pytest

from psd2demo.iban import mask, normalize, validate


@pytest.mark.parametrize("value", ["BE68539007547034", "NL91ABNA0417164300", "DE89370400440532013000"])
def test_valid(value):
    validate(value)


def test_normalize():
    assert normalize(" be68 5390 0754 7034 ") == "BE68539007547034"


@pytest.mark.parametrize("value,err", [
    ("BE68539007547035", "checksum"),
    ("BE6853900754703", "16 characters"),
    ("XX", "format"),
    ("BE68-5390", "format"),
])
def test_invalid(value, err):
    with pytest.raises(ValueError, match=err):
        validate(value)


def test_mask():
    assert mask("BE68539007547034") == "BE68 **** **** 7034"
    assert "0417" not in mask("NL91ABNA0417164300")
    assert mask("NL91ABNA0417164300").endswith("4300")
