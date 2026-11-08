"""Human-readable rendering of transactions and summary."""

import json
import os
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from psd2demo.transactions import Summary, Txn

COLUMNS = ("Booked", "Value", "Amount", "Cur", "Counterparty", "Remittance", "Status", "Ref")
MIN_REMITTANCE = 10


def _fmt_amount(amount: Decimal | None) -> str:
    return "-" if amount is None else f"{amount:+,.2f}"


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(width - 1, 0)] + "…"


def render_transactions(txns: list[Txn], width: int | None = None) -> str:
    if not txns:
        return "No transactions in range."
    width = width or shutil.get_terminal_size((120, 24)).columns
    rows = []
    for t in txns:
        rows.append([
            (t.booking_date.isoformat() if t.booking_date else "-") + ("*" if t.out_of_range else ""),
            t.value_date.isoformat() if t.value_date else "-",
            _fmt_amount(t.amount),
            t.currency or "-",
            _clip(t.counterparty_name or "-", 28),
            t.remittance or "-",
            t.status or "-",
            _clip(t.reference or "-", 16),
        ])
    widths = [max(len(COLUMNS[i]), *(len(r[i]) for r in rows)) for i in range(len(COLUMNS))]
    fixed = sum(w for i, w in enumerate(widths) if i != 5) + 2 * (len(COLUMNS) - 1)
    widths[5] = max(MIN_REMITTANCE, min(widths[5], width - fixed))
    right = {2}

    def line(cells):
        out = []
        for i, c in enumerate(cells):
            c = _clip(c, widths[i]) if i == 5 else c
            out.append(c.rjust(widths[i]) if i in right else c.ljust(widths[i]))
        return "  ".join(out).rstrip()

    lines = [line(COLUMNS), line(["-" * w for w in widths]), *(line(r) for r in rows)]
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


def export_json(path: Path, meta: dict, txns: list[Txn], raw_pages: list[dict]) -> None:
    """Write normalized transactions plus raw API pages; file mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {**meta, "generated_at": datetime.now(timezone.utc).isoformat()},
        "transactions": [
            {**asdict(t), "effective_date": t.effective_date} for t in txns
        ],
        "raw_pages": raw_pages,
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
