"""
ES256/JWKS verification in app/api/deps.py, without touching the network:
tokens are signed with a locally generated P-256 key, and the JWKS client's
fetch is stubbed to serve that key's public half — the same shape Supabase's
/auth/v1/.well-known/jwks.json returns.
"""
import json
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api import deps
from app.core.config import get_settings

SUPABASE_URL = "https://example-ref.supabase.co"
ISSUER = f"{SUPABASE_URL}/auth/v1"
KID = "test-key-id"


def _jwk(private_key: ec.EllipticCurvePrivateKey) -> dict:
    public_jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private_key.public_key()))
    return {**public_jwk, "kid": KID, "alg": "ES256", "use": "sig"}


def _token(private_key: ec.EllipticCurvePrivateKey, **overrides) -> str:
    claims = {
        "sub": str(uuid.uuid4()),
        "email": "user@example.com",
        "aud": "authenticated",
        "iss": ISSUER,
        "exp": int(time.time()) + 300,
        **overrides,
    }
    return jwt.encode(claims, private_key, algorithm="ES256", headers={"kid": KID})


def _authenticate(token: str) -> deps.CurrentUser:
    return deps.get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))


@pytest.fixture
def signing_key(monkeypatch):
    private_key = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    get_settings.cache_clear()
    deps._jwks_client.cache_clear()
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: {"keys": [_jwk(private_key)]})
    yield private_key
    get_settings.cache_clear()
    deps._jwks_client.cache_clear()


def test_valid_es256_token_resolves_user(signing_key):
    subject = str(uuid.uuid4())
    user = _authenticate(_token(signing_key, sub=subject))
    assert str(user.id) == subject
    assert user.email == "user@example.com"


def test_token_from_another_issuer_is_rejected(signing_key):
    with pytest.raises(HTTPException) as exc:
        _authenticate(_token(signing_key, iss="https://other-ref.supabase.co/auth/v1"))
    assert exc.value.status_code == 401


def test_token_signed_by_unknown_key_is_rejected(signing_key):
    forged = _token(ec.generate_private_key(ec.SECP256R1()))
    with pytest.raises(HTTPException) as exc:
        _authenticate(forged)
    assert exc.value.status_code == 401
