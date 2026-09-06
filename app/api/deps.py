"""
Shared FastAPI dependencies: DB session + the Basic Auth guard that
protects every route per the architecture diagram.

Upgrade path (per diagram annotation "upgrade path: JWT / sessions"):
swap `get_current_user`'s internals for a bearer-token lookup without
touching the route signatures, since routes only depend on this function.
"""
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.db.models import User
from app.db.session import get_db

security = HTTPBasic()


def get_current_user(
    credentials: HTTPBasicCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    user = db.query(User).filter(User.username == credentials.username).first()

    # Always run verify_password, even for a missing user, so response timing
    # doesn't leak whether a username exists.
    valid_password = verify_password(
        credentials.password, user.hashed_password if user else "$2b$12$invalidsaltinvalidsaltin"
    )
    username_matches = user is not None and secrets.compare_digest(credentials.username, user.username)

    if not (username_matches and valid_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return user
