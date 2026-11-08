"""Single-record local session store (JSON file, user-only permissions, atomic writes)."""

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)
SCHEMA_VERSION = 1


def iban_hash(iban: str) -> str:
    return hashlib.sha256(iban.encode()).hexdigest()


@dataclass(frozen=True)
class StoredSession:
    session_id: str
    app_id: str
    bank_name: str
    bank_country: str
    iban_sha256: str
    account_uid: str
    valid_until: str
    created_at: str

    def matches(self, app_id: str, bank_name: str, bank_country: str, iban: str) -> bool:
        return (self.app_id, self.bank_name.casefold(), self.bank_country, self.iban_sha256) == (
            app_id, bank_name.casefold(), bank_country, iban_hash(iban))


class SessionStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> StoredSession | None:
        try:
            data = json.loads(self.path.read_text())
            if data.pop("schema_version", None) != SCHEMA_VERSION:
                raise ValueError("unsupported schema_version")
            return StoredSession(**data)
        except FileNotFoundError:
            return None
        except (ValueError, TypeError) as exc:
            log.warning("ignoring unreadable session file %s: %s", self.path, exc)
            return None

    def save(self, session: StoredSession) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".session-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:  # mkstemp creates the file with mode 0600
                json.dump({"schema_version": SCHEMA_VERSION, **asdict(session)}, fh, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
