import jwt
import pytest

from conftest import APP_ID
from psd2demo.eb.auth import JwtSigner


def test_header_and_claims(pem_bytes, rsa_key):
    signer = JwtSigner(APP_ID, pem_bytes, ttl=600, clock=lambda: 1_000_000)
    token = signer.token()
    header = jwt.get_unverified_header(token)
    assert header == {"alg": "RS256", "typ": "JWT", "kid": APP_ID}
    claims = jwt.decode(token, rsa_key.public_key(), algorithms=["RS256"], audience="api.enablebanking.com",
                        options={"verify_exp": False, "verify_iat": False})
    assert claims == {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": 1_000_000,
                      "exp": 1_000_600}


@pytest.mark.parametrize("ttl", [0, 86401])
def test_ttl_bounds(pem_bytes, ttl):
    with pytest.raises(ValueError):
        JwtSigner(APP_ID, pem_bytes, ttl=ttl)


def test_cached_until_near_expiry(pem_bytes):
    now = [1_000_000]
    signer = JwtSigner(APP_ID, pem_bytes, ttl=600, clock=lambda: now[0])
    first = signer.token()
    now[0] += 500
    assert signer.token() == first
    now[0] += 50  # 50 s left < 60 s margin
    assert signer.token() != first


def test_bad_key():
    with pytest.raises(ValueError):
        JwtSigner(APP_ID, b"not a key")
