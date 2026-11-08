"""Synchronous Enable Banking API client."""

import logging
import time
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Callable, Iterator

import httpx

from psd2demo.eb import errors
from psd2demo.eb.auth import JwtSigner
from psd2demo.eb.models import (AccountDetails, Aspsp, AuthStart, Balance, Session, SessionStatus,
                                TransactionsPage)

log = logging.getLogger(__name__)

MAX_RETRIES = 3
MAX_RETRY_AFTER = 30.0


def _retry_after_seconds(value: str | None) -> float | None:
    try:
        return min(float(value), MAX_RETRY_AFTER) if value else None
    except ValueError:
        return None


class EnableBankingClient:
    BASE_URL = "https://api.enablebanking.com"

    def __init__(self, signer: JwtSigner, *, base_url: str = BASE_URL, timeout: float = 30.0,
                 psu_headers: dict[str, str] | None = None, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep, event_hooks: dict | None = None):
        self._signer = signer
        self.psu_headers = psu_headers or {}  # sent on /auth, /sessions and AIS data calls
        self.request_count = 0  # HTTP attempts made, including retries
        self._sleep = sleep
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport,
                                  headers={"Accept": "application/json"}, event_hooks=event_hooks)

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- transport ---------------------------------------------------------

    def _request(self, method: str, path: str, *, params=None, json=None, psu: bool = False) -> dict:
        headers = {"Authorization": f"Bearer {self._signer.token()}"}
        if psu:
            headers.update(self.psu_headers)
        for attempt in range(MAX_RETRIES + 1):
            resp, retry_after, reason = None, None, None
            self.request_count += 1
            try:
                resp = self._http.request(method, path, params=params, json=json, headers=headers)
            except httpx.TransportError as exc:
                if method != "GET" or attempt == MAX_RETRIES:
                    raise errors.ApiNetworkError(0, method, path, message=f"network error: {exc}") from exc
                reason = type(exc).__name__
            else:
                if resp.is_success:
                    if not resp.content:  # e.g. 204 on DELETE
                        return {}
                    try:
                        return resp.json()
                    except ValueError:
                        raise errors.ApiSchemaError(resp.status_code, method, path,
                                                    message="unexpected response: not JSON")
                retryable = resp.status_code == 429 or (method == "GET" and resp.status_code >= 500)
                if not retryable or attempt == MAX_RETRIES:
                    raise errors.from_response(resp)
                retry_after, reason = resp.headers.get("Retry-After"), f"HTTP {resp.status_code}"
            delay = _retry_after_seconds(retry_after) or 2.0 ** attempt
            log.info("retry %d/%d after %s in %.1fs", attempt + 1, MAX_RETRIES, reason, delay)
            self._sleep(delay)
        raise AssertionError("unreachable")

    def _parse(self, model, method: str, path: str, data):
        try:
            return model.from_json(data)
        except (ValueError, TypeError, AttributeError) as exc:
            raise errors.ApiSchemaError(200, method, path, message=f"unexpected response: {exc}") from exc

    # -- endpoints ---------------------------------------------------------

    def get_aspsps(self, country: str, psu_type: str = "personal") -> list[Aspsp]:
        data = self._request("GET", "/aspsps", params={"country": country, "psu_type": psu_type})
        return [self._parse(Aspsp, "GET", "/aspsps", a) for a in data.get("aspsps") or []]

    def start_auth(self, aspsp: Aspsp, *, valid_until: datetime, state: str, redirect_url: str,
                   psu_type: str = "personal") -> AuthStart:
        body = {
            "access": {"valid_until": valid_until.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")},
            "aspsp": {"name": aspsp.name, "country": aspsp.country},
            "state": state,
            "redirect_url": redirect_url,
            "psu_type": psu_type,
        }
        return self._parse(AuthStart, "POST", "/auth", self._request("POST", "/auth", json=body, psu=True))

    def create_session(self, code: str) -> Session:
        data = self._request("POST", "/sessions", json={"code": code}, psu=True)
        return self._parse(Session, "POST", "/sessions", data)

    def get_session(self, session_id: str) -> SessionStatus:
        path = f"/sessions/{session_id}"
        return self._parse(SessionStatus, "GET", path, self._request("GET", path))

    def delete_session(self, session_id: str) -> None:
        """Revoke the session and close the consent at the bank (not retried except on 429)."""
        self._request("DELETE", f"/sessions/{session_id}")

    def get_balances(self, account_uid: str) -> list[Balance]:
        path = f"/accounts/{account_uid}/balances"
        data = self._request("GET", path, psu=True)
        return [self._parse(Balance, "GET", path, b) for b in data.get("balances") or []]

    def get_account_details(self, account_uid: str) -> AccountDetails:
        path = f"/accounts/{account_uid}/details"
        return self._parse(AccountDetails, "GET", path, self._request("GET", path, psu=True))

    def get_transactions_page(self, account_uid: str, date_from: date, date_to: date,
                              continuation_key: str | None = None) -> TransactionsPage:
        path = f"/accounts/{account_uid}/transactions"
        params = {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()}
        if continuation_key:
            params["continuation_key"] = continuation_key
        return self._parse(TransactionsPage, "GET", path, self._request("GET", path, params=params, psu=True))

    def iter_transactions(self, account_uid: str, date_from: date, date_to: date, *,
                          max_pages: int = 50) -> Iterator[TransactionsPage]:
        seen: set[str] = set()
        key = None
        for n in range(1, max_pages + 1):
            page = self.get_transactions_page(account_uid, date_from, date_to, key)
            key = page.continuation_key
            if not key:
                yield page
                return
            if key in seen or n == max_pages:
                reason = "repeated continuation_key" if key in seen else f"page limit {max_pages}"
                log.warning("stopped transaction paging (%s); more data may be available", reason)
                yield replace(page, truncated=True)
                return
            seen.add(key)
            yield page
