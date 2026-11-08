"""Per-account history cache: one JSON file per (account, range, detail level), user-only permissions.

It holds full own-account IBANs and transactions, so it lives under the git-ignored state dir with
0700/0600 modes, like exports.
"""

import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from enablebanking_psd2.fileio import write_text_atomic

log = logging.getLogger(__name__)
CACHE_VERSION = 1


class HistoryCache:
    def __init__(self, directory: Path):
        self.directory = directory

    @staticmethod
    def key(app_id: str, iban_sha256: str, date_from: date, date_to: date, include: frozenset[str]) -> str:
        parts = [app_id, iban_sha256, date_from.isoformat(), date_to.isoformat(), ",".join(sorted(include))]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str, max_age_min: int | None, now: datetime) -> dict | None:
        """The cached account entry, if at most ``max_age_min`` minutes old (None: any age)."""
        path = self._path(key)
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict) or data.get("cache_version") != CACHE_VERSION:
                raise ValueError("unsupported cache file")
            stored_at = datetime.fromisoformat(data["stored_at"])
            entry = data["entry"]
        except FileNotFoundError:
            return None
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("ignoring unreadable cache file %s: %s", path, exc)
            return None
        if max_age_min is not None and now - stored_at > timedelta(minutes=max_age_min):
            return None
        return entry

    def put(self, key: str, entry: dict, now: datetime) -> None:
        write_text_atomic(self._path(key), json.dumps(
            {"cache_version": CACHE_VERSION, "stored_at": now.isoformat(), "entry": entry}))

    def prune(self, older_than: timedelta, now: datetime) -> int:
        """Delete cache files not written within ``older_than``; returns how many."""
        removed = 0
        for path in self.directory.glob("*.json") if self.directory.is_dir() else ():
            if datetime.fromtimestamp(path.stat().st_mtime, now.tzinfo) < now - older_than:
                path.unlink(missing_ok=True)
                removed += 1
        return removed
