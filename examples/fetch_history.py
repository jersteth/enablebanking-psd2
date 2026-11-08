"""Spending per currency over the last 30 days, using the Python API.

    pipx install enablebanking-psd2     # or: pip install enablebanking-psd2
    enablebanking init                  # once
    enablebanking authorize             # once per bank, about every 180 days
    python fetch_history.py
"""

from collections import defaultdict
from decimal import Decimal

from enablebanking_psd2 import fetch_history


def spending(doc: dict) -> dict[str, Decimal]:
    """Money out per currency over the accounts that were fetched (amounts are signed decimal strings)."""
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for account in doc["accounts"]:
        if account["status"] != "ok":
            continue
        for t in account["transactions"]:
            if t["amount"].startswith("-") and not t["out_of_range"]:
                totals[t["currency"]] += -Decimal(t["amount"])
    return dict(totals)


def main() -> None:
    doc = fetch_history(days=30)  # raises only ConfigError; per-account problems are inside the document
    for account in doc["accounts"]:
        if account["status"] == "consent_required":
            print(f"{account['name']}: {account['error']['hint']}")  # e.g. "enablebanking authorize --account main"
        elif account["status"] != "ok":
            print(f"{account['name']}: {account['status']} - {account['error']['message']}")
    for currency, total in spending(doc).items():
        print(f"spent {total:.2f} {currency} in {doc['range']['date_from']}..{doc['range']['date_to']}")


if __name__ == "__main__":
    main()
