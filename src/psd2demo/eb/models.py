"""Typed views over Enable Banking API responses. Unknown fields are ignored.

``from_json`` raises ``ValueError`` on an unexpected shape; the client wraps it into ``ApiSchemaError``.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation


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
    bic: str | None = None
    beta: bool = False

    @property
    def max_consent_days(self) -> int | None:
        return self.maximum_consent_validity // 86400 if self.maximum_consent_validity else None

    @classmethod
    def from_json(cls, d: dict) -> "Aspsp":
        return cls(name=_require(d, "name"), country=_require(d, "country"),
                   maximum_consent_validity=d.get("maximum_consent_validity"),
                   required_psu_headers=list(d.get("required_psu_headers") or []),
                   bic=d.get("bic"), beta=bool(d.get("beta")))


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
    cash_account_type: str | None = None

    @classmethod
    def from_json(cls, d: dict) -> "Account":
        return cls(uid=_require(d, "uid"), iban=(d.get("account_id") or {}).get("iban"),
                   name=d.get("name") or d.get("product"), currency=d.get("currency"),
                   cash_account_type=d.get("cash_account_type"))


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


def _decimal(value) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except InvalidOperation:
        return None


@dataclass(frozen=True)
class Balance:
    name: str | None
    amount: Decimal | None
    currency: str | None
    balance_type: str | None
    reference_date: date | None
    last_change: datetime | None
    last_committed_transaction: str | None

    @classmethod
    def from_json(cls, d: dict) -> "Balance":
        if not isinstance(d, dict):
            raise ValueError("balance is not an object")
        amount = d.get("balance_amount") or {}
        ref = d.get("reference_date")
        return cls(name=d.get("name"), amount=_decimal(amount.get("amount")), currency=amount.get("currency"),
                   balance_type=d.get("balance_type"), reference_date=date.fromisoformat(ref[:10]) if ref else None,
                   last_change=_dt(d.get("last_change_date_time")),
                   last_committed_transaction=d.get("last_committed_transaction"))


@dataclass(frozen=True)
class AccountDetails:
    """Account resource; the fields vary per bank, so the raw object is kept and flattened for display."""
    raw: dict

    @classmethod
    def from_json(cls, d: dict) -> "AccountDetails":
        if not isinstance(d, dict):
            raise ValueError("account details are not an object")
        return cls(raw=d)
