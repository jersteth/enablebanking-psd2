"""IBAN normalization, validation and masking."""

import re

IBAN_RE = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}")
_COUNTRY_LENGTHS = {"BE": 16}


def normalize(value: str) -> str:
    return "".join(value.split()).upper()


def validate(value: str) -> None:
    """Raise ValueError if ``value`` (already normalized) is not a valid IBAN."""
    if not IBAN_RE.fullmatch(value):
        raise ValueError(f"IBAN {mask(value)} has an invalid format")
    expected = _COUNTRY_LENGTHS.get(value[:2])
    if expected and len(value) != expected:
        raise ValueError(f"IBAN {mask(value)} must be {expected} characters for {value[:2]}")
    rearranged = value[4:] + value[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    if int(digits) % 97 != 1:
        raise ValueError(f"IBAN {mask(value)} has an invalid checksum")


def mask(value: str) -> str:
    """``BE68539007547034`` -> ``BE68 **** **** 7034``."""
    if len(value) < 8:
        return "*" * len(value)
    middle_groups = max(1, (len(value) - 8 + 3) // 4)
    return " ".join([value[:4], *(["****"] * middle_groups), value[-4:]])
