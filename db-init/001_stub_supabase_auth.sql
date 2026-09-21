-- Local dev only. The real Supabase project already has an `auth` schema
-- (managed by Supabase Auth / GoTrue) with a full auth.users table — this
-- is NOT used in staging/prod, only against the plain postgres:16 image in
-- docker-compose.yml, which has no such schema.
--
-- app.saved_queries and app.recent_queries both carry a foreign key to
-- auth.users(id) (see alembic/versions/e362816aec7f_switch_to_supabase_auth.py),
-- so a completely bare local Postgres can't even run `alembic upgrade head`
-- without this table existing first — the FK has nothing to reference.
-- This stub is just enough shape to satisfy that FK for local development;
-- it has none of Supabase's real columns, triggers, or auth logic.
CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE IF NOT EXISTS auth.users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email text
);

-- A stable test user so local manual testing (see scripts/make_test_jwt.py)
-- has a real row to reference without having to insert one by hand.
INSERT INTO auth.users (id, email)
VALUES ('00000000-0000-0000-0000-000000000001', 'test.investigator@local.dev')
ON CONFLICT (id) DO NOTHING;
