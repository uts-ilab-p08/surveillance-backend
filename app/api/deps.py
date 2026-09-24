"""
Auth dependency — verifies a Supabase-issued JWT and resolves the current
user from it. This backend does not own identity: the frontend logs users
in directly against Supabase Auth (GoTrue), and every request here carries
that token as "Authorization: Bearer <token>". There is no local users
table, no password hashing, and no /auth/register or /auth/login route —
Supabase's own client SDK/REST API covers those on the frontend's side.

Verification: this project's Supabase Auth already runs on asymmetric JWT
Signing Keys (ES256), not the older single HS256 shared secret (confirmed
in the Supabase dashboard — JWT Keys -> JWT Signing Keys shows an ECC
P-256 key as current, with the old HS256 shared secret demoted to a
"previous key" kept only to verify tokens issued before the migration).
So real tokens are verified against Supabase's own public JWKS endpoint
(see _jwks_client below) rather than a secret configured here at all —
there's nothing sensitive to request or leak for this path.
https://supabase.com/docs/guides/auth/signing-keys has the background.

Local dev fallback: when SUPABASE_URL isn't configured (e.g. no real
Supabase project access yet), this falls back to verifying an HS256 token
signed with LOCAL_DEV_JWT_SECRET instead — see scripts/make_test_jwt.py.
That secret is local-only, unrelated to anything in the real Supabase
project, and never used once SUPABASE_URL is set.

We only need enough of the token to know *who* is calling, for per-user
data (saved_queries, recent_queries) — both FK straight to Supabase's own
auth.users(id), not to anything this backend manages.
"""
import uuid
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

bearer_scheme = HTTPBearer()

AUDIENCE = "authenticated"  # Supabase's default JWT audience claim


class CurrentUser:
    """Just enough identity to scope a query to one user. Not a DB model —
    there's no local users table to back it with."""

    def __init__(self, id: uuid.UUID, email: str | None):
        self.id = id
        self.email = email


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    """One cached client for the process's lifetime. PyJWKClient itself
    caches the fetched key set in memory (and handles re-fetching if a
    token references a key id it hasn't seen), so this just avoids
    rebuilding the client itself on every request."""
    settings = get_settings()
    jwks_url = f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    return jwt.PyJWKClient(jwks_url, cache_keys=True)


def _decode(token: str) -> dict:
    settings = get_settings()

    if settings.supabase_url:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience=AUDIENCE,
            issuer=f"{settings.supabase_url.rstrip('/')}/auth/v1",
        )

    # Local dev fallback — see module docstring. Never reached once
    # SUPABASE_URL is configured.
    return jwt.decode(token, settings.local_dev_jwt_secret, algorithms=["HS256"], audience=AUDIENCE)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> CurrentUser:
    try:
        payload = _decode(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    subject = payload.get("sub")
    if not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing subject")

    return CurrentUser(id=uuid.UUID(subject), email=payload.get("email"))
