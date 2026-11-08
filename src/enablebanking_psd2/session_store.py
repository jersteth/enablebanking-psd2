"""Local session store: one consent record per bank (JSON file, user-only permissions, atomic writes).

Schema v2: ``{"schema_version": 2, "sessions": [record, ...]}``. A 002 v1 file (single record) is
migrated on read; it is rewritten as v2 only on the next write.
"""

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Literal

from enablebanking_psd2 import iban as iban_mod
from enablebanking_psd2.config import AccountSpec
from enablebanking_psd2.fileio import write_text_atomic

log = logging.getLogger(__name__)
SCHEMA_VERSION = 2


def iban_hash(iban: str) -> str:
    return hashlib.sha256(iban_mod.normalize(iban).encode()).hexdigest()


@dataclass(frozen=True)
class StoredAccount:
    uid: str
    iban_sha256: str
    # Display-only metadata captured at consent time, so `accounts` needs no bank calls. Never a full IBAN.
    iban_masked: str = ""
    name: str | None = None
    currency: str | None = None
    cash_account_type: str | None = None


@dataclass(frozen=True)
class BankMeta:
    maximum_consent_validity: int | None = None
    required_psu_headers: tuple[str, ...] = ()


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    app_id: str
    bank_name: str
    bank_country: str
    accounts: tuple[StoredAccount, ...]
    valid_until: str  # ISO 8601, "" if unknown
    created_at: str
    status: Literal["active", "revoked"] = "active"
    bank: BankMeta | None = None  # None for records migrated from v1

    def account_uid(self, iban: str) -> str | None:
        wanted = iban_hash(iban)
        return next((a.uid for a in self.accounts if a.iban_sha256 == wanted), None)

    def valid_until_dt(self) -> datetime | None:
        try:
            return datetime.fromisoformat(self.valid_until) if self.valid_until else None
        except ValueError:
            return None

    def valid_at(self, now: datetime, margin: timedelta = timedelta(0)) -> bool:
        until = self.valid_until_dt()
        return self.status == "active" and until is not None and until > now + margin

    @property
    def short_id(self) -> str:
        return self.session_id[:8]

    def local_status(self, now: datetime) -> str:
        return "revoked" if self.status == "revoked" else "active" if self.valid_at(now) else "expired"

    def covers(self, app_id: str, account: AccountSpec) -> bool:
        return self.same_bank(app_id, account.bank_name, account.bank_country) and bool(self.account_uid(account.iban))

    def same_bank(self, app_id: str, bank_name: str, bank_country: str) -> bool:
        return (self.app_id, self.bank_name.casefold(), self.bank_country.upper()) == (
            app_id, bank_name.casefold(), bank_country.upper())

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "SessionRecord":
        bank = d.get("bank")
        return cls(**{**d, "accounts": tuple(StoredAccount(**a) for a in d["accounts"]),
                      "bank": BankMeta(**{**bank, "required_psu_headers": tuple(bank["required_psu_headers"])})
                      if bank else None})

    @classmethod
    def from_v1(cls, d: dict) -> "SessionRecord":
        return cls(session_id=d["session_id"], app_id=d["app_id"], bank_name=d["bank_name"],
                   bank_country=d["bank_country"], accounts=(StoredAccount(d["account_uid"], d["iban_sha256"]),),
                   valid_until=d.get("valid_until") or "", created_at=d.get("created_at") or "")


class SessionStore:
    def __init__(self, path: Path):
        self.path = path
        self._unreadable = False

    def records(self) -> list[SessionRecord]:
        """All records; empty (with a warning) if the file is missing or unreadable."""
        self._unreadable = False
        try:
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
            version = data.get("schema_version")
            if version == 1:
                return [SessionRecord.from_v1(data)]
            if version != SCHEMA_VERSION:
                raise ValueError(f"unsupported schema_version {version!r}")
            return [SessionRecord.from_json(r) for r in data.get("sessions") or []]
        except FileNotFoundError:
            return []
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            log.warning("ignoring unreadable session file %s: %s", self.path, exc)
            self._unreadable = True
            return []

    def for_bank(self, app_id: str, bank_name: str, bank_country: str) -> SessionRecord | None:
        return next((r for r in self.records() if r.same_bank(app_id, bank_name, bank_country)), None)

    def for_account(self, app_id: str, account: AccountSpec) -> SessionRecord | None:
        return next((r for r in self.records() if r.covers(app_id, account)), None)

    def upsert(self, record: SessionRecord) -> None:
        """Insert or replace the record of the same (app_id, bank, country)."""
        others = [r for r in self.records()
                  if not r.same_bank(record.app_id, record.bank_name, record.bank_country)]
        self._write([*others, record])

    def mark_revoked(self, record: SessionRecord) -> SessionRecord:
        revoked = replace(record, status="revoked")
        self.upsert(revoked)
        return revoked

    def remove(self, predicate: Callable[[SessionRecord], bool]) -> int:
        records = self.records()
        keep = [r for r in records if not predicate(r)]
        if len(keep) != len(records):
            self._write(keep)
        return len(records) - len(keep)

    def _write(self, records: list[SessionRecord]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self._unreadable and self.path.exists():  # never silently replace a file we couldn't read
            backup = self.path.with_name(self.path.name + ".bak")
            os.replace(self.path, backup)
            log.warning("unreadable session file kept as %s", backup)
            self._unreadable = False
        write_text_atomic(self.path, json.dumps(
            {"schema_version": SCHEMA_VERSION, "sessions": [r.to_json() for r in records]}, indent=2))
