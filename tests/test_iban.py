import pytest

from enablebanking_psd2.iban import mask, normalize, validate


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


@pytest.mark.parametrize("value", [
    "NL91ABNA0417164300", "DE89370400440532013000", "FR1420041010050500013M02606", "LU280019400644750000",
    "MT84MALT011000012345MTLCAST001S", "NO9386011117947", "GB29NWBK60161331926819", "BE68539007547034"])
def test_valid_ibans_of_other_countries(value):
    validate(value)


@pytest.mark.parametrize("value,match", [
    ("NL91ABNA041716430", "must be 18 characters for NL"),
    ("DE89370400440532013", "must be 22 characters for DE"),
    ("NO93860111179470", "must be 15 characters for NO"),
    ("NL92ABNA0417164300", "invalid checksum")])
def test_wrong_length_or_checksum_is_rejected(value, match):
    with pytest.raises(ValueError, match=match):
        validate(value)
