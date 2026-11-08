"""Enable Banking API errors."""

import httpx

# Error codes meaning "the resource is already gone"; reported as ApiNotFound like a 404.
GONE_ERRORS = {"SESSION_NOT_FOUND", "ALREADY_CLOSED"}

ERROR_HINTS = {
    "ACCESS_DENIED": "consent denied or account not linked to the application",
    "ASPSP_ERROR": "bank-side error; retry later",
    "PSU_HEADER_NOT_PROVIDED": "the bank requires all of its PSU headers or none; set psu_ip_address "
                               "(your public IP) in config.toml or pass --psu-ip",
}


class ApiError(Exception):
    default_hint: str | None = None

    def __init__(self, status: int, method: str, endpoint: str, *, message: str | None = None,
                 detail=None, error: str | None = None, code: int | str | None = None):
        self.status = status
        self.code = code
        self.method = method
        self.endpoint = endpoint
        self.message = message or f"HTTP {status}"
        self.detail = detail
        self.error = error
        self.hint: str | None = ERROR_HINTS.get(error) or self.default_hint  # callers may refine it
        super().__init__(f"{method} {endpoint} failed: {self.message}")

    def describe(self) -> str:
        parts = [f"status={self.status}"] if self.status else []
        parts.append(f"endpoint={self.method} {self.endpoint}")
        if self.code is not None and self.code != self.status:
            parts.append(f"code={self.code}")
        if self.error:
            parts.append(f"error={self.error}")
        if self.detail:
            parts.append(f"detail={self.detail}")
        return " ".join(parts)


class ApiAuthError(ApiError):
    default_hint = ("check app_id (JWT kid) matches the key, the system clock is correct, "
                    "and the application is Active (linked accounts) in the control panel")


class ApiRateLimited(ApiError):
    default_hint = "rate limited by Enable Banking or the bank; PSD2 allows banks to cap unattended access at 4/day"


class ApiNotFound(ApiError):
    pass


class ApiServerError(ApiError):
    default_hint = "Enable Banking or bank-side error; retry later"


class ApiNetworkError(ApiError):
    default_hint = "check your internet connection and that api.enablebanking.com is reachable"


class ApiSchemaError(ApiError):
    pass


def from_response(resp: httpx.Response) -> ApiError:
    try:
        body = resp.json()
    except ValueError:
        body = {"message": resp.text[:500] or None}
    if not isinstance(body, dict):
        body = {"detail": body}
    status = resp.status_code
    cls = (ApiAuthError if status in (401, 403) else ApiRateLimited if status == 429
           else ApiNotFound if status == 404 or body.get("error") in GONE_ERRORS
           else ApiServerError if status >= 500 else ApiError)
    return cls(status, resp.request.method, resp.request.url.path, message=body.get("message"),
               detail=body.get("detail"), error=body.get("error"), code=body.get("code"))
