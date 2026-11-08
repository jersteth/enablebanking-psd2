"""Human-readable rendering of transactions and summary."""

import json
import os
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from psd2demo.eb.models import Aspsp, Balance
from psd2demo.transactions import Summary, Txn

COLUMNS = ("Booked", "Value", "Amount", "Cur", "Counterparty", "Counterparty IBAN", "Remittance", "Status", "Ref")
REMITTANCE = COLUMNS.index("Remittance")
MIN_REMITTANCE = 10
BALANCE_LABELS = {
    "CLBD": "closing booked", "ITAV": "interim available", "ITBD": "interim booked", "XPCD": "expected",
    "CLAV": "closing available", "OPBD": "opening booked", "FWAV": "forward available",
}


def _fmt_amount(amount: Decimal | None) -> str:
    return "-" if amount is None else f"{amount:+,.2f}"


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(width - 1, 0)] + "…"


def _table(columns: tuple[str, ...], rows: list[list[str]], *, width: int | None = None,
           flex: int | None = None, right: frozenset[int] = frozenset(), min_flex: int = 10) -> list[str]:
    """Aligned text table. Column ``flex`` shrinks (with an ellipsis) to fit ``width``; ``right`` columns are
    right-aligned."""
    width = width or shutil.get_terminal_size((120, 24)).columns
    widths = [max(len(columns[i]), *(len(r[i]) for r in rows)) for i in range(len(columns))]
    if flex is not None:
        fixed = sum(w for i, w in enumerate(widths) if i != flex) + 2 * (len(columns) - 1)
        widths[flex] = max(min_flex, min(widths[flex], width - fixed))

    def line(cells):
        out = []
        for i, c in enumerate(cells):
            c = _clip(c, widths[i]) if i == flex else c
            out.append(c.rjust(widths[i]) if i in right else c.ljust(widths[i]))
        return "  ".join(out).rstrip()

    return [line(columns), line(["-" * w for w in widths]), *(line(r) for r in rows)]


def render_transactions(txns: list[Txn], width: int | None = None) -> str:
    if not txns:
        return "No transactions in range."
    rows = [[
        (t.booking_date.isoformat() if t.booking_date else "-") + ("*" if t.out_of_range else ""),
        t.value_date.isoformat() if t.value_date else "-",
        _fmt_amount(t.amount),
        t.currency or "-",
        _clip(t.counterparty_name or "-", 28),
        t.counterparty_iban or "-",
        t.remittance or "-",
        t.status or "-",
        _clip(t.reference or "-", 16),
    ] for t in txns]
    lines = _table(COLUMNS, rows, width=width, flex=REMITTANCE, right=frozenset({2}), min_flex=MIN_REMITTANCE)
    if any(t.out_of_range for t in txns):
        lines.append("* returned by the bank outside the requested date range")
    return "\n".join(lines)


def render_summary(summary: dict[str, Summary]) -> str:
    if not summary:
        return "Summary: no transactions."
    lines = ["Summary:"]
    for cur, s in summary.items():
        lines.append(f"  {cur}: {s.count} transactions, credits {_fmt_amount(s.credits)}, "
                     f"debits {_fmt_amount(s.debits)}, net {_fmt_amount(s.net)}")
    return "\n".join(lines)


def write_json(path: Path, payload: dict) -> None:
    """Write ``payload`` as JSON with file mode 0600, creating the parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)


def _meta(meta: dict) -> dict:
    return {**meta, "generated_at": datetime.now(timezone.utc).isoformat()}


def export_json(path: Path, meta: dict, txns: list[Txn], raw_pages: list[dict]) -> None:
    """Normalized transactions plus raw API pages."""
    write_json(path, {"meta": _meta(meta),
                      "transactions": [{**asdict(t), "effective_date": t.effective_date} for t in txns],
                      "raw_pages": raw_pages})


def export_balances(path: Path, meta: dict, balances: list[Balance]) -> None:
    write_json(path, {"meta": _meta(meta), "balances": [asdict(b) for b in balances]})


def balance_label(code: str | None) -> str:
    return BALANCE_LABELS.get(code or "", "")


def render_banks(banks: list[Aspsp], width: int | None = None) -> str:
    if not banks:
        return "No banks match."
    rows = [[b.name, b.bic or "-", f"{b.maximum_consent_validity // 86400} d" if b.maximum_consent_validity else "-",
             ", ".join(b.required_psu_headers) or "-", "beta" if b.beta else ""] for b in banks]
    return "\n".join(_table(("Bank", "BIC", "Max consent", "Required PSU headers", ""), rows, width=width,
                             flex=3, right=frozenset({2})))


def render_balances(balances: list[Balance], width: int | None = None) -> str:
    if not balances:
        return "The bank returned no balances."
    rows = [[b.balance_type or "-", balance_label(b.balance_type) or "-", b.name or "-", _fmt_amount(b.amount),
             b.currency or "-", b.reference_date.isoformat() if b.reference_date else "-",
             b.last_change.isoformat(timespec="minutes") if b.last_change else "-"] for b in balances]
    return "\n".join(_table(("Type", "Meaning", "Name", "Amount", "Cur", "Reference date", "Last change"), rows,
                             width=width, flex=2, right=frozenset({3})))


def render_accounts(rows: list[dict], width: int | None = None) -> str:
    """``rows``: dicts with selected, iban, name, currency, cash_account_type (as built by the accounts command)."""
    table = [["*" if r["selected"] else "", r["iban"] or "-", r["name"] or "-", r["currency"] or "-",
              r["cash_account_type"] or "-"] for r in rows]
    lines = _table(("", "IBAN", "Name", "Cur", "Type"), table, width=width, flex=2)
    lines.append("* the selected account; other IBANs are masked")
    return "\n".join(lines)


def flatten(data, prefix: str = "") -> dict[str, str]:
    """Nested dicts become ``a.b`` keys, lists are joined with ", "; empty values are dropped."""
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(data, list):
        parts = [", ".join(f"{k}={v}" for k, v in flatten(x).items()) if isinstance(x, (dict, list)) else str(x)
                 for x in data if x not in (None, "", [], {})]
        if parts:
            out[prefix] = "; ".join(parts)
    elif data not in (None, ""):
        out[prefix] = str(data)
    return out


def render_details(details: dict[str, str], width: int | None = None) -> str:
    if not details:
        return "The bank returned no account details."
    return "\n".join(_table(("Field", "Value"), [[k, v] for k, v in details.items()], width=width, flex=1))


def render_sessions(rows: list[dict], width: int | None = None) -> str:
    if not rows:
        return "No stored sessions."
    table = [[r["bank"], r["country"], r["session"], str(r["accounts"]), ", ".join(r["configured"]) or "-",
              r["valid_until"] or "-", r["local_status"], r["live_status"] or "(offline)"] for r in rows]
    return "\n".join(_table(("Bank", "Cty", "Session", "Accts", "Configured accounts", "Valid until", "Local",
                              "Live"), table, width=width, flex=4))
