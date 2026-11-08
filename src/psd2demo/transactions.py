"""Normalization, sorting and summary of Enable Banking transactions."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation


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


def _amount(raw: dict, debit: bool) -> tuple[Decimal | None, str | None]:
    ta = raw.get("transaction_amount") or {}
    try:
        amount = abs(Decimal(str(ta["amount"]))) if ta.get("amount") is not None else None
    except InvalidOperation:
        amount = None
    if amount is not None and debit:
        amount = -amount
    return amount, ta.get("currency")


def normalize(raw: dict, date_from: date, date_to: date) -> Txn:
    debit = raw.get("credit_debit_indicator") == "DBIT"
    amount, currency = _amount(raw, debit)
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


def summarize(txns: list[Txn]) -> dict[str, Summary]:
    acc: dict[str, list] = {}
    for t in txns:
        if t.amount is None:
            continue
        cur = t.currency or "?"
        count, credits, debits = acc.get(cur, [0, Decimal(0), Decimal(0)])
        if t.amount >= 0:
            credits += t.amount
        else:
            debits += t.amount
        acc[cur] = [count + 1, credits, debits]
    return {cur: Summary(*vals) for cur, vals in sorted(acc.items())}
