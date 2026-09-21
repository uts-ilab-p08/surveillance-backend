"""
Auth dependency — verifies a Supabase-issued JWT and resolves the current
user from it. This backend does not own identity: the frontend logs users
in directly against Supabase Auth (GoTrue), and every request here carries
that token as "Authorization: Bearer <token>". There is no local users
table, no password hashing, and no /auth/register or /auth/login route —
Supabase's own client SDK/REST API covers those on the frontend's side.

We only need enough of the token to know *who* is calling, for per-user
data (saved_queries, recent_queries) — both FK straight to Supabase's own
auth.users(id), not to anything this backend manages.
"""
import uuid

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

bearer_scheme = HTTPBearer()


class CurrentUser:
    """Just enough identity to scope a query to one user. Not a DB model —
    there's no local users table to back it with."""

    def __init__(self, id: uuid.UUID, email: str | None):
        self.id = id
        self.email = email


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> CurrentUser:
    settings = get_settings()
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",  # Supabase's default JWT audience claim
        )
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
