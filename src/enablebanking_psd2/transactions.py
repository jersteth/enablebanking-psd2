"""Normalization, sorting and summary of Enable Banking transactions."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable


@dataclass(frozen=True)
class Txn:
    booking_date: date | None
    value_date: date | None
    transaction_date: date | None
    amount: Decimal | None
    currency: str | None
    counterparty_name: str | None
    counterparty_iban: str | None
    remittance: str | None
    status: str | None
    reference: str | None
    out_of_range: bool

    @property
    def effective_date(self) -> date | None:
        return self.booking_date or self.value_date or self.transaction_date


@dataclass(frozen=True)
class Summary:
    count: int
    credits: Decimal
    debits: Decimal

    @property
    def net(self) -> Decimal:
        return self.credits + self.debits


def _date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _amount(raw: dict) -> tuple[Decimal | None, str | None]:
    """Signed amount: ``credit_debit_indicator`` decides the sign; without it the bank's own sign is kept."""
    ta = raw.get("transaction_amount") or {}
    try:
        amount = Decimal(str(ta["amount"])) if ta.get("amount") is not None else None
    except InvalidOperation:
        amount = None
    indicator = str(raw.get("credit_debit_indicator") or "").upper()
    if amount is not None and indicator in ("CRDT", "DBIT"):
        amount = -abs(amount) if indicator == "DBIT" else abs(amount)
    return amount, ta.get("currency")


def normalize(raw: dict, date_from: date, date_to: date) -> Txn:
    amount, currency = _amount(raw)
    debit = (amount is not None and amount < 0) or str(raw.get("credit_debit_indicator") or "").upper() == "DBIT"
    party = raw.get("creditor" if debit else "debtor") or {}
    party_account = raw.get("creditor_account" if debit else "debtor_account") or {}
    booking, value, txn_date = (_date(raw.get(k)) for k in ("booking_date", "value_date", "transaction_date"))
    effective = booking or value or txn_date
    return Txn(
        booking_date=booking,
        value_date=value,
        transaction_date=txn_date,
        amount=amount,
        currency=currency,
        counterparty_name=party.get("name"),
        counterparty_iban=party_account.get("iban"),
        remittance=" / ".join(r for r in (raw.get("remittance_information") or []) if r) or None,
        status=raw.get("status"),
        reference=raw.get("entry_reference") or raw.get("transaction_id"),
        out_of_range=effective is not None and not (date_from <= effective <= date_to),
    )


def sort_txns(txns: list[Txn]) -> list[Txn]:
    return sorted(txns, key=lambda t: t.effective_date or date.min, reverse=True)


def summarize_amounts(pairs: Iterable[tuple[str | None, Decimal | None]]) -> dict[str, Summary]:
    """Per-currency count, credits (>= 0) and debits (< 0) of (currency, amount) pairs; None amounts skipped."""
    acc: dict[str, list] = {}
    for currency, amount in pairs:
        if amount is None:
            continue
        count, credits, debits = acc.get(currency or "?", [0, Decimal(0), Decimal(0)])
        acc[currency or "?"] = [count + 1, credits + amount, debits] if amount >= 0 else [count + 1, credits,
                                                                                          debits + amount]
    return {cur: Summary(*vals) for cur, vals in sorted(acc.items())}


def summarize(txns: list[Txn]) -> dict[str, Summary]:
    return summarize_amounts((t.currency, t.amount) for t in txns)
