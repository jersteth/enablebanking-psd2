"""RS256 JWT signing for Enable Banking API requests."""

import time
from typing import Callable

import jwt
from cryptography.hazmat.primitives.serialization import load_pem_private_key

MAX_TTL = 86400
RESIGN_MARGIN = 60


class JwtSigner:
    def __init__(self, app_id: str, private_key_pem: bytes, ttl: int = 3600,
                 clock: Callable[[], float] = time.time):
        if not 1 <= ttl <= MAX_TTL:
            raise ValueError(f"JWT ttl must be within 1..{MAX_TTL} seconds")
        self.app_id = app_id
        self._key = load_pem_private_key(private_key_pem, password=None)
        self._ttl = ttl
        self._clock = clock
        self._token: str | None = None
        self._exp = 0

    def token(self) -> str:
        now = int(self._clock())
        if self._token is None or self._exp - now < RESIGN_MARGIN:
            self._exp = now + self._ttl
            claims = {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": now, "exp": self._exp}
            self._token = jwt.encode(claims, self._key, algorithm="RS256", headers={"kid": self.app_id})
        return self._token
