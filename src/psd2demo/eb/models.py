"""Typed views over Enable Banking API responses. Unknown fields are ignored.

``from_json`` raises ``ValueError`` on an unexpected shape; the client wraps it into ``ApiSchemaError``.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _dt(value) -> datetime | None:
    """Parse an ISO 8601 timestamp; one without an offset is taken as UTC so comparisons never fail."""
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _require(data: dict, key: str):
    if not isinstance(data, dict) or not data.get(key):
        raise ValueError(f"missing {key!r}")
    return data[key]


@dataclass(frozen=True)
class Aspsp:
    name: str
    country: str
    maximum_consent_validity: int | None = None
    required_psu_headers: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, d: dict) -> "Aspsp":
        return cls(name=_require(d, "name"), country=_require(d, "country"),
                   maximum_consent_validity=d.get("maximum_consent_validity"),
                   required_psu_headers=list(d.get("required_psu_headers") or []))


@dataclass(frozen=True)
class AuthStart:
    url: str
    authorization_id: str | None

    @classmethod
    def from_json(cls, d: dict) -> "AuthStart":
        return cls(url=_require(d, "url"), authorization_id=d.get("authorization_id"))


@dataclass(frozen=True)
class Account:
    uid: str
    iban: str | None
    name: str | None
    currency: str | None

    @classmethod
    def from_json(cls, d: dict) -> "Account":
        return cls(uid=_require(d, "uid"), iban=(d.get("account_id") or {}).get("iban"),
                   name=d.get("name") or d.get("product"), currency=d.get("currency"))


@dataclass(frozen=True)
class Session:
    session_id: str
    accounts: list[Account]
    valid_until: datetime | None

    @classmethod
    def from_json(cls, d: dict) -> "Session":
        return cls(session_id=_require(d, "session_id"),
                   accounts=[Account.from_json(a) for a in d.get("accounts") or []],
                   valid_until=_dt((d.get("access") or {}).get("valid_until")))


@dataclass(frozen=True)
class SessionStatus:
    status: str
    account_uids: list[str]
    valid_until: datetime | None

    @classmethod
    def from_json(cls, d: dict) -> "SessionStatus":
        accounts = d.get("accounts") or []
        uids = [a if isinstance(a, str) else a.get("uid") for a in accounts]
        return cls(status=_require(d, "status"), account_uids=[u for u in uids if u],
                   valid_until=_dt((d.get("access") or {}).get("valid_until")))


@dataclass(frozen=True)
class TransactionsPage:
    transactions: list[dict]
    continuation_key: str | None
    raw: dict = field(repr=False, compare=False)
    truncated: bool = False

    @classmethod
    def from_json(cls, d: dict) -> "TransactionsPage":
        if not isinstance(d, dict) or not isinstance(d.get("transactions", []), list):
            raise ValueError("missing 'transactions' list")
        return cls(transactions=d.get("transactions") or [], continuation_key=d.get("continuation_key") or None,
                   raw=d)
