"""The account-history JSON contract (schema v1): serialization in one place.

Dicts are built in schema key order; values are JSON-native (amounts as signed decimal strings, dates as
YYYY-MM-DD, datetimes as UTC ``...Z``, missing values as ``None`` with the key kept). A breaking change
needs SCHEMA_VERSION "2" and a new ``schemas/account-history.v2.json`` (inside this package).
"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Sequence

from enablebanking_psd2.config import AccountSpec
from enablebanking_psd2.eb.models import Balance
from enablebanking_psd2.flow import Step
from enablebanking_psd2.transactions import Txn, summarize_amounts

SCHEMA_VERSION = "1"
TOOL_NAME = "psd2-demo"
STATUSES = ("ok", "consent_required", "rate_limited", "error")
# error.step values: the flow steps that establish the session report as one "SESSION" step.
_SESSION_STEPS = {Step.CONFIG, Step.BANK, Step.CONSENT, Step.ACCOUNT}


def schema_path(version: str = SCHEMA_VERSION) -> Traversable:
    """The published JSON Schema, shipped inside the package (works in wheels and editable installs)."""
    return resources.files(__package__) / "schemas" / f"account-history.v{version}.json"


def error_step(step: Step) -> str:
    return "SESSION" if step in _SESSION_STEPS else step.value


def amount(value: Decimal | None) -> str | None:
    """``Decimal("-42.5")`` → ``"-42.50"`` style: plain digits, explicit sign, never a float or exponent."""
    if value is None:
        return None
    text = format(value, "f")
    return text if text.startswith("-") else "+" + text


def day(value: date | None) -> str | None:
    return value.isoformat() if value else None


def timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash_key(iban: str, t: Txn) -> tuple[str, ...]:
    return (iban, day(t.effective_date) or "", amount(t.amount) or "", t.currency or "", t.counterparty_name or "",
            t.counterparty_iban or "", t.remittance or "")


def stable_id(account_iban: str, txn: Txn, ordinal: int) -> str:
    """The bank's reference when present, else a hash of the transaction's content plus ``ordinal`` (its index
    among identical transactions in API order), so two identical payments on one day get different ids."""
    if txn.reference:
        return "ref:" + txn.reference
    digest = hashlib.sha256("|".join([*_hash_key(account_iban, txn), str(ordinal)]).encode()).hexdigest()
    return "h:" + digest[:32]


def transaction(txn: Txn, txn_id: str, raw: dict | None = None, *, with_raw: bool = False) -> dict:
    out = {
        "id": txn_id,
        "booking_date": day(txn.booking_date),
        "value_date": day(txn.value_date),
        "transaction_date": day(txn.transaction_date),
        "amount": amount(txn.amount),
        "currency": txn.currency,
        "counterparty_name": txn.counterparty_name,
        "counterparty_iban": txn.counterparty_iban,
        "remittance": txn.remittance,
        "status": txn.status,
        "reference": txn.reference,
        "out_of_range": txn.out_of_range,
    }
    if with_raw:
        out["raw"] = raw
    return out


def transactions(account: AccountSpec, txns: list[Txn], raws: list[dict] | None = None) -> list[dict]:
    """``txns`` in API order (with matching ``raws`` when given); returned newest first with stable ids."""
    seen: Counter = Counter()
    rows = []
    for i, t in enumerate(txns):  # ordinals are assigned in API order, before sorting
        key = _hash_key(account.iban, t)
        rows.append((t, stable_id(account.iban, t, seen[key]), raws[i] if raws is not None else None))
        seen[key] += 1
    rows.sort(key=lambda r: r[0].effective_date or date.min, reverse=True)  # newest first, stable like sort_txns
    return [transaction(t, txn_id, raw, with_raw=raws is not None) for t, txn_id, raw in rows]


def balance(b: Balance) -> dict:
    return {
        "name": b.name,
        "type": b.balance_type,
        "type_label": b.type_label,
        "amount": amount(b.amount),
        "currency": b.currency,
        "reference_date": day(b.reference_date),
        "last_change": timestamp(b.last_change),
    }


def main_currency(txns: list[dict], balances: list[dict] | None) -> str | None:
    counts = Counter(x["currency"] for x in [*txns, *(balances or [])] if x.get("currency"))
    return counts.most_common(1)[0][0] if counts else None


def account_entry(*, account: AccountSpec, status: str, bank: str | None = None, error: dict | None = None,
                  warnings: Sequence[dict] = (), session_valid_until: str | None = None,
                  fetched_at: datetime | None = None, truncated: bool = False, cached: bool = False,
                  stale: bool = False, api_calls: int = 0, transactions: Sequence[dict] = (),
                  balances: list[dict] | None = None, details: dict | None = None,
                  include: frozenset[str] = frozenset()) -> dict:
    assert status in STATUSES, status
    txns = list(transactions)
    entry = {
        "name": account.name,
        "bank": bank or account.bank_name,
        "country": account.bank_country,
        "iban": account.iban,
        "currency": main_currency(txns, balances),
        "status": status,
        "error": error,
        "warnings": list(warnings),
        "session_valid_until": session_valid_until,
        "fetched_at": timestamp(fetched_at),
        "truncated": truncated,
        "cached": cached,
        "stale": stale,
        "api_calls": api_calls,
        "transactions": txns,
    }
    if "balances" in include:
        entry["balances"] = balances
    if "details" in include:
        entry["details"] = details
    return entry


def error(step: str, message: str, *, http_status: int | None = None, api_error: str | None = None,
          hint: str | None = None) -> dict:
    return {"step": step, "message": message, "http_status": http_status or None, "api_error": api_error,
            "hint": hint}


def _totals(entries: list[dict]) -> dict:
    """Per currency over the ok entries (cached ones included)."""
    totals = summarize_amounts((t["currency"], Decimal(t["amount"]) if t["amount"] is not None else None)
                               for e in entries if e["status"] == "ok" for t in e["transactions"])
    return {cur: {"count": s.count, "credits": amount(s.credits), "debits": amount(s.debits), "net": amount(s.net)}
            for cur, s in totals.items()}


def document(*, generated_at: datetime, tool_version: str, date_from: date, date_to: date,
             include: frozenset[str], entries: list[dict]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": timestamp(generated_at),
        "tool": {"name": TOOL_NAME, "version": tool_version},
        "range": {"date_from": day(date_from), "date_to": day(date_to)},
        "include": sorted(include),
        "accounts": entries,
        "summary": {
            "accounts": {s: sum(1 for e in entries if e["status"] == s) for s in STATUSES},
            "totals": _totals(entries),
        },
    }
