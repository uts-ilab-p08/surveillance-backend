"""
Password hashing + HTTP Basic Auth verification.

Per the architecture diagram, MVP auth is Basic Auth (username + password)
guarding every API route, with a documented upgrade path to JWT/sessions
once there's a reason to need real sessions (rate limiting, expiry, etc).
Passwords are always hashed at rest with bcrypt regardless of the auth
scheme used to transmit them, so the JWT upgrade later is a routing change,
not a data-model change.
"""
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)
