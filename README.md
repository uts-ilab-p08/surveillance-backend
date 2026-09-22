# Surveillance Video Query API — Backend

This is the FastAPI service behind the Interactive Surveillance Video
Querying project. The product flow is: a user logs in via Supabase Auth on
the frontend, types a natural-language query, and this backend verifies
their session, forwards the query to the RAG service, and whatever RAG
sends back — the top handful of matching video clips, each with a
relevance score — goes straight to the frontend. This repo owns the
search relay and the read-only clip/camera/query-history layer around it.
It does **not** own user identity — see "Auth" below.

It deliberately does **not** run a vision-language model, does not touch a
vector store, and does not call an LLM directly — that's the RAG team's
service. It also doesn't store or serve video files itself: raw footage,
annotations, and playback URLs live in the annotation team's `bronze`
schema and Cloudflare R2 bucket
([video-annotation-pipeline](https://github.com/uts-ilab-p08/video-annotation-pipeline.git),
metadata/CV pipeline in
[videometa](https://github.com/uts-ilab-p08/videometa.git)). There's no
user-facing video upload in this product, so this backend has no video
data of its own to manage.

## How it fits together

- **Annotation pipeline repo** — a set of notebooks (not yet a live
  service) that pull MEVA clips, run motion/object detection and a local
  VLM over them, and load the results into the shared Supabase project's
  `bronze` schema, with the actual video files hosted on Cloudflare R2.
- **RAG repo** — vector search + LLM. `GET /search` forwards the query
  there and passes the response straight back, including each result's
  video reference, playback URL, and relevance score. Retrieval and
  generation both happen on their side, including the user-facing answer
  text.
- **This repo** — the search relay, plus a read layer over `bronze` for
  clip/camera detail and per-user query history.
- **Supabase Auth (GoTrue)** — owns identity entirely. The frontend logs
  users in directly against Supabase; this backend only verifies the JWT
  it issues.

The RAG integration works without that service running: leave
`RAG_SERVICE_URL` unset and `/search` falls back to a mock response.
Useful for developing or demoing this repo on its own — flip the URL on
once RAG is up, no code changes needed.

## Auth

There is no local `users` table, no password hashing, and no
`/auth/login` or `/auth/register` route in this backend. Identity is
entirely Supabase Auth's: the frontend authenticates users directly
against Supabase (client SDK or REST API), and every request to this API
carries the resulting session token as `Authorization: Bearer <token>`.

This project's Supabase Auth runs on asymmetric JWT Signing Keys (ES256),
not the older single shared secret — confirmed in the dashboard under
JWT Keys -> JWT Signing Keys (current key: ECC P-256; the old HS256
shared secret shows up there only as a "previous key", kept around to
verify tokens issued before the project migrated). So `app/api/deps.py`
verifies real tokens against Supabase's own public JWKS endpoint
(`SUPABASE_URL` + `/auth/v1/.well-known/jwks.json`) rather than a secret
at all — there's nothing sensitive to be handed for this to work, just
the project's base URL. See
[Supabase's JWT Signing Keys docs](https://supabase.com/docs/guides/auth/signing-keys).

When `SUPABASE_URL` isn't set (e.g. no real Supabase project access yet),
`app/api/deps.py` falls back to verifying an HS256 token signed with
`LOCAL_DEV_JWT_SECRET` instead — see "Local testing" below. That fallback
secret is local-only and unrelated to anything in the real Supabase
project; it's never used once `SUPABASE_URL` is set.

`saved_queries.user_id` and `recent_queries.user_id` are foreign keys
straight to Supabase's own `auth.users(id)` — a table this backend doesn't
migrate, just references, since it already exists in the same Supabase
project.

This was a switch from an earlier local Basic-Auth/bcrypt design; that
table and code path are gone (see the `e362816aec7f` migration).

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in SUPABASE_URL from the Supabase dashboard ->
# Project Settings -> General -> Project URL

# local Postgres (dev only — in staging/prod the DB_* fields in .env
# point at the shared Supabase project instead)
docker compose up -d

# first time only: apply migrations
alembic upgrade head

uvicorn app.main:app --reload
```

Everything lives under `/api/v1` except `/health`. Swagger docs at `/docs`
once it's running. Authenticated routes expect a Supabase-issued JWT —
get one by logging in through Supabase Auth on the frontend side (or the
Supabase REST API directly, for manual testing) and pass it as
`Authorization: Bearer <token>`.

## Endpoints

| Endpoint | What it does |
|---|---|
| `GET /search` | Forwards a natural-language query to the RAG service. **Not yet updated for the real RAG contract** — still returns RAG's raw `answer`/`sources` reshaped a bit, not the full `Clip[]` the frontend spec expects. Blocked on confirming the RAG response shape (see "Talking to the RAG repo"). |
| `GET /clips/{id}` | Fetches one clip directly from the annotation team's `bronze` schema, by `event_id`. Independent of the RAG contract — built and working. |
| `GET /clips/{id}/related` | Nearby clips on the same camera, closest in time first (our own heuristic — the frontend contract doesn't specify one). |
| `GET /cameras` | The Cameras Directory modal — camera code, scene, and event count, aggregated from `bronze`. |
| `GET /queries/recent` | A user's recent searches. **Currently always empty** — nothing writes to it yet, since that write is a side effect of `POST /search`, which isn't wired to real RAG results yet either. |
| `GET /queries/saved` | A user's bookmarked searches. |
| `POST /queries/saved` | Bookmarks a query. `hits` starts at 0 and is meant to increment when that same query is re-run through `POST /search` — that increment logic isn't wired up yet (same blocker as above). |
| `GET /health` | Plain liveness check, no auth — for uptime monitors and deploy platforms. |

All routes above except `/health` require a valid Supabase JWT.

**`confidence` is RAG's raw relevance score, unscaled (0–1 float), passed
through exactly as received — no `* 100` conversion.** If the frontend
wants a percentage, it converts it client-side. Note this diverges from
the frontend spec's own written §2.2 normalization table
(`confidence = round(score * 100)`) — flagged to the frontend team,
pending their sign-off on updating that doc to match.

Not built yet, on purpose: rate limiting, request logging middleware,
retry/backoff on the outbound RAG call, and the two conversational
assistant endpoints (`POST /assistant/query`, `POST /assistant/summarize-clip`)
the frontend spec adds for Results/Clip Detail — on hold pending the
team's weekly connect (along with the `thumbnailUrl` question). Video
upload and the annotation hand-off (`POST /videos/upload`, `POST
/annotate`) stay removed — the product doesn't let users upload footage.

## Talking to the RAG repo

This one contract is the entire coupling between this repo and the RAG
repo — and it's also the whole product's search path (user query -> this
endpoint -> RAG -> top-K videos with relevance scores -> frontend). If
the shape needs to change, it's a conversation with that team first.

**RAG service** (`app/services/rag_client.py`):
```
Backend -> POST {RAG_SERVICE_URL}/query
           {"query": "...", "limit": 10}
RAG     -> 200 {"answer": "...",
                "results": [{"video_id": "<bronze video_id, a sha256 hex
                              string — not a UUID>",
                             "video_url": "<public R2 URL, if available>",
                             "start_seconds": 0.0, "end_seconds": 10.0,
                             "caption": "...", "score": 0.83}, ...]}
```

The corresponding schema lives in `app/schemas/rag.py`
(`RagQueryResult`), so a shape change is a one-file diff on this side.
Open question with Abhishek (RAG): whether each result also includes
`event_id`, not just `video_id` — without it, this backend can't reliably
resolve which event in a multi-event video matched (see
`app/services/bronze.py`'s four-hop bronze traversal). Also confirmed:
RAG owns the full user-facing answer text; this backend's own LLM usage,
if any, is scoped to the separate `/assistant/*` endpoints only, not
`/search`.

## Project layout

```
app/
  main.py              FastAPI app, router wiring, CORS
  core/
    config.py          Settings (env vars / .env)
  db/
    models.py           SQLAlchemy models (SavedQuery, RecentQuery — no
                         local User model; see "Auth")
    session.py           Engine + per-request session dependency
  schemas/               Pydantic request/response models (incl. rag.py —
                          the RAG contract, clip.py — the frontend's Clip
                          shape)
  api/
    deps.py               Supabase JWT verification dependency (JWKS/ES256, with a local-only HS256 fallback)
    routes/               search, clips, cameras, queries, health
  services/
    rag_client.py            HTTP client -> RAG repo
    bronze.py                Read-only queries over the annotation team's
                              bronze schema
    clip_builder.py           Builds the frontend's Clip shape from a
                              bronze event + a confidence value
    tagging.py                Maps bronze object_types/event_name onto the
                              frontend's closed ClipTag enum
alembic/                  DB migrations
tests/
```

## Local testing without real Supabase project access

You don't need Supabase project access to develop against this backend.
`db-init/001_stub_supabase_auth.sql` seeds a minimal `auth.users` stub
(just enough shape to satisfy the FK on `saved_queries`/`recent_queries`)
into your local docker-compose Postgres on first startup, including one
test user (`00000000-0000-0000-0000-000000000001`). `scripts/make_test_jwt.py`
mints a fake-but-correctly-shaped Supabase JWT for that user, signed with
`LOCAL_DEV_JWT_SECRET` from your local `.env`. This only works while
`SUPABASE_URL` is unset — once it's set, app/api/deps.py verifies real
tokens against Supabase's own JWKS endpoint instead (see "Auth" above),
and this local fallback path is never used.

```bash
docker compose up -d      # first run seeds the auth.users stub automatically
alembic upgrade head
uvicorn app.main:app --reload &

python scripts/make_test_jwt.py    # prints a token + a ready-to-run curl example
```

This only works locally — the test user id isn't in the real Supabase
project's `auth.users`, so routes that write to `saved_queries`/
`recent_queries` would hit a foreign key violation there. Once you have
real project access, get a real token by logging in through the frontend
(or Supabase's API) instead.

If your local Postgres container already existed before this migration
(i.e. `pgdata` isn't a fresh volume), the init script won't retroactively
run — either `docker compose down -v && docker compose up -d` to reseed
from scratch, or insert the stub row into `auth.users` yourself.

## Testing it end-to-end

This runs every route against a real Postgres instance, with the RAG
service left on its built-in mock:

```bash
alembic upgrade head
uvicorn app.main:app --reload &

TOKEN="<a Supabase-issued JWT, e.g. from logging in on the frontend>"
AUTH="-H \"Authorization: Bearer $TOKEN\""

# search (mocked until RAG_SERVICE_URL is set)
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/search?q=did+anyone+enter+the+building"

# unit tests
pytest tests/ -q
```

## Deploying to Render

`render.yaml` is a Blueprint — Render reads it to auto-configure the
service instead of you clicking through settings by hand. One-time setup:

1. Push this repo to GitHub (already done — `main` is up to date).
2. In the Render dashboard: **New +** -> **Blueprint** -> connect the
   `surveillance-backend` GitHub repo. Render detects `render.yaml`
   automatically.
3. Render will prompt you for the env vars marked `sync: false` in
   `render.yaml` — these are the secrets/project-specific values it can't
   read from the repo (and shouldn't: `.env` is gitignored on purpose).
   Fill in the same values you have locally in `.env`:
   `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME` (the shared
   Supabase project's pooler connection details),
   `SUPABASE_URL` (the project's base URL — not a secret, see "Auth" above),
   `RAG_SERVICE_URL` (leave blank for now — `/search`
   falls back to its mock response until this is set, same as local dev),
   and `CORS_ORIGINS` (the frontend's deployed URL, once they have one;
   `http://localhost:3000` won't work for a deployed frontend talking to a
   deployed backend).
4. Deploy. Render runs `alembic upgrade head` before starting the server
   on every deploy (see `startCommand` in `render.yaml`) — safe to leave
   as-is, since re-running already-applied migrations is a no-op.
5. `GET https://<your-service>.onrender.com/health` should return
   `{"status": "ok"}` once it's up. Share the base URL with the frontend
   team so they can stop pointing at `localhost`.

Render's free tier spins the service down after inactivity and takes
~30-60s to wake back up on the next request — fine for this stage of the
project, but worth knowing if a demo's first request looks slow.

## What's next

1. Confirm the RAG contract with Abhishek — specifically, whether each of
   the 5 results includes `event_id`. (Message sent; awaiting reply.)
2. Once that's confirmed: rewrite `POST /search` to call RAG, build a
   `Clip` per result via `app/services/clip_builder.py`, and write the
   `recent_queries` / `saved_queries.hits` side effects described in the
   frontend spec.
3. Decide, with the frontend team, whether `thumbnailUrl` needs a
   backend/pipeline-produced image at all, or whether the frontend can
   derive a poster frame client-side from `videoUrl` — on hold for the
   weekly connect.
4. Build the two conversational assistant endpoints — also on hold for
   the weekly connect, once an LLM provider is picked.
5. Deploy this API somewhere with a real, reachable URL (Render/Railway
   are reasonable free-tier options) so the frontend isn't stuck pointing
   at `localhost`.
