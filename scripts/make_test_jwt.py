"""
Mint a fake Supabase-shaped JWT for LOCAL DEVELOPMENT ONLY, so you can hit
this backend's authenticated routes before real Supabase project access
(and the real SUPABASE_JWT_SECRET) is available.

This does NOT talk to Supabase at all — it just signs a token with the same
shape Supabase's own JWTs have (sub / aud / exp claims), using HS256 and
whatever LOCAL_DEV_JWT_SECRET is currently in your local .env. This only
works when SUPABASE_URL is UNSET in your .env: app/api/deps.py verifies
tokens against Supabase's real JWKS endpoint (ES256) whenever SUPABASE_URL
is configured, and only falls back to this local HS256 secret when it
isn't — see that file's docstring for why (Supabase now signs real tokens
with an asymmetric key, not a shared secret).

IMPORTANT: this only works against your LOCAL docker-compose Postgres,
which has a stub auth.users row seeded by db-init/001_stub_supabase_auth.sql
(id 00000000-0000-0000-0000-000000000001). It will NOT work against the
real shared Supabase project, because that user id doesn't exist in
Supabase's real auth.users table — routes that write to saved_queries/
recent_queries (which have an FK to auth.users) would fail there with a
foreign key violation. Once you have real Supabase project access, get a
real token by logging in through the frontend (or Supabase's own API)
instead of this script.

Usage:
    python scripts/make_test_jwt.py
    python scripts/make_test_jwt.py --user-id <uuid> --email someone@example.com
"""
import argparse
import time
import uuid

import jwt

from app.core.config import get_settings

DEFAULT_TEST_USER_ID = "00000000-0000-0000-0000-000000000001"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", default=DEFAULT_TEST_USER_ID, help="Must exist in auth.users for FK-writing routes to work.")
    parser.add_argument("--email", default="test.investigator@local.dev")
    parser.add_argument("--expires-in", type=int, default=86400, help="Seconds until the token expires (default 24h).")
    args = parser.parse_args()

    # Validate it's a real UUID up front — a bad --user-id fails loudly here
    # instead of as a confusing 401 later.
    uuid.UUID(args.user_id)

    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": args.user_id,
        "email": args.email,
        "aud": "authenticated",
        "iat": now,
        "exp": now + args.expires_in,
    }
    token = jwt.encode(payload, settings.local_dev_jwt_secret, algorithm="HS256")

    print(token)
    print()
    print("Try it:")
    print(f'  curl -H "Authorization: Bearer {token}" http://localhost:8000/api/v1/queries/saved')


if __name__ == "__main__":
    main()
