"""Sample account-history documents built from fixtures, for schema tests and schemas/*.example.json.

Regenerate the example after a contract change:  .venv/bin/python tests/history_docs.py
"""

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from conftest import FOREIGN_IBAN, OTHER_IBAN, VALID_IBAN, load_fixture  # noqa: E402
from psd2demo import contract  # noqa: E402
from psd2demo.config import HISTORY_PARTS, AccountSpec  # noqa: E402
from psd2demo.eb.models import Balance  # noqa: E402
from psd2demo.transactions import normalize  # noqa: E402

EXAMPLE = Path(__file__).parent.parent / "schemas" / "account-history.v1.example.json"
GENERATED = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
FROM, TO = date(2026, 10, 9), date(2026, 11, 8)
MAIN = AccountSpec("main", "KBC", "BE", VALID_IBAN)
SAVINGS = AccountSpec("savings", "KBC", "BE", OTHER_IBAN)
GIRO = AccountSpec("giro", "Belfius", "BE", FOREIGN_IBAN)


def _ok_entry(include: frozenset[str], truncated: bool = False) -> dict:
    raws = load_fixture("transactions_p1.json")["transactions"]
    txns = [normalize(r, FROM, TO) for r in raws]
    balances = [contract.balance(Balance.from_json(b)) for b in load_fixture("balances.json")["balances"][:2]]
    return contract.account_entry(
        account=MAIN, bank="KBC", status="ok", session_valid_until="2027-05-07T10:00:00+00:00",
        fetched_at=GENERATED, api_calls=4,
        transactions=contract.transactions(MAIN, txns, raws if "raw" in include else None),
        balances=balances, details={"product": "KBC Plus", "cash_account_type": "CACC"}, include=include,
        truncated=truncated,
        warnings=[{"part": "transactions", "message": "stopped paging (page limit 50); more data may be available",
                   "http_status": None}] if truncated else [])


def _consent_required_entry(include: frozenset[str] = frozenset()) -> dict:
    return contract.account_entry(account=SAVINGS, status="consent_required", include=include,
                                  error=contract.error("SESSION", "consent required for account 'savings' (KBC)",
                                                       hint="psd2-demo authorize --account savings"))


def scenarios() -> dict[str, dict]:
    """name → document, covering every status and detail level."""
    full = HISTORY_PARTS
    entries = {
        "ok": _ok_entry(frozenset()),
        "ok_full": _ok_entry(full, truncated=True),
        "consent_required": _consent_required_entry(),
        "rate_limited": contract.account_entry(
            account=GIRO, bank="Belfius", status="rate_limited",
            error=contract.error("TRANSACTIONS", "Too many requests", http_status=429,
                                 api_error="ASPSP_RATE_LIMIT_EXCEEDED")),
        "error": contract.account_entry(account=GIRO, bank="Belfius", status="error",
                                        error=contract.error("internal", "ValueError: boom")),
    }
    def doc(include, *names):
        return contract.document(generated_at=GENERATED, tool_version="0.1.0", date_from=FROM, date_to=TO,
                                 include=include, entries=[entries[n] for n in names])
    return {
        "all_ok": doc(frozenset(), "ok"),
        "full_detail": doc(full, "ok_full"),
        "partial": doc(frozenset(), "ok", "consent_required", "rate_limited"),
        "all_failed": doc(frozenset(), "error", "consent_required"),
        "empty": doc(frozenset()),
    }


def example() -> dict:
    """The committed example: a partial run with balances and details."""
    include = frozenset({"balances", "details"})
    return contract.document(generated_at=GENERATED, tool_version="0.1.0", date_from=FROM, date_to=TO,
                             include=include, entries=[_ok_entry(include), _consent_required_entry(include)])


def dump(doc: dict) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    EXAMPLE.write_text(dump(example()))
    print(f"wrote {EXAMPLE}")
