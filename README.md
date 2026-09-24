# Surveillance Video Query API — Backend

This is the FastAPI service behind the Interactive Surveillance Video
Querying project. The product flow is: a user logs in via Supabase Auth on
the frontend, types a natural-language query, and this backend verifies
their session, hands the query to the RAG package, and whatever RAG
returns — the top handful of matching video clips, each with a
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

RAG runs in-process, as a pip dependency (the `ilabs-cctv-rag` package,
imported as `rag`) — not as a separate service. How to install, configure,
upgrade and debug it: "Talking to the RAG repo" below.

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
| `GET /search` | Runs a natural-language query through the RAG package (`rag.pipeline.answer_query`) and returns its `answer` plus up to `limit` matches (RAG itself returns at most 5). **Not yet the full `Clip[]` the frontend spec expects** — see "Talking to the RAG repo". |
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

The backend talks to RAG **through a Python package, not over HTTP**. The
RAG team's repo
([uts-ilab-p08/iLabs-capstone-rag](https://github.com/uts-ilab-p08/iLabs-capstone-rag))
is installed as a regular pip dependency and called in-process — there is
no separate RAG service to deploy, and no URL to configure.

```
GET /search?q=...                       (app/api/routes/search.py)
  -> rag_client.query(q, limit)         (app/services/rag_client.py — adapter)
    -> rag.pipeline.answer_query(q)     (the package, same process)
      -> fastembed: embed the query     (model weights cached on disk)
      -> Qdrant: nearest events         (remote cluster, QDRANT_URL)
    <- {"query", "answer", "sources"}
  <- RagQueryResult {"answer", "results"}  -> frontend
```

This one function call is the entire coupling between the two repos — and
it's also the whole product's search path. If the shape needs to change,
it's a conversation with the RAG team first.

### Installing it

`requirements.txt` pins the package to a git tag:

```
ilabs-cctv-rag @ git+https://github.com/uts-ilab-p08/iLabs-capstone-rag.git@vX.Y.Z
```

- **Distribution name** (what pip sees): `ilabs-cctv-rag`.
  **Module name** (what Python imports): `rag`. Import `rag.pipeline`,
  never `ilabs_cctv_rag`.
- The repo is public, so `pip install -r requirements.txt` works locally
  and on Render with no GitHub credentials.
- pip builds the package from the tagged commit itself (there's no PyPI
  release), so the installed code is exactly what that tag points at.

Check the install worked:

```bash
DATABASE_URL=postgresql://x:y@localhost/z \
  python -c "from rag.pipeline import answer_query; print('RAG package OK')"
```

(`DATABASE_URL` only has to be *set* for this check — the package reads it
at import time.)

**Working on RAG and the backend together:** clone the RAG repo next to
this one and install it editable, so changes there show up here without
publishing a tag:

```bash
pip install -e ../iLabs-capstone-rag
```

Run `pip install -r requirements.txt` again to go back to the pinned tag.

### Configuring it

The package reads its **own** settings straight from the process
environment (it calls `load_dotenv()`, so a local `.env` works too) — not
from `app/core/config.py`. Set these next to the backend's own vars:

| Variable | Example | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql://USER:PASSWORD@HOST:5432/postgres` | Supabase connection string. **Required** — the package fails to import without it |
| `DB_SCHEMA` | `bronze` | Schema holding the annotation team's events/videos |
| `QDRANT_URL` | `https://<cluster>.qdrant.cloud` | **Must be set outside local dev.** Empty = embedded `./qdrant_data`, which Render wipes on every deploy |
| `QDRANT_API_KEY` | | Qdrant Cloud API key |
| `QDRANT_COLLECTION` | `meva_events` | |
| `EMBED_MODEL` | `BAAI/bge-base-en-v1.5` | Must match the model the collection was indexed with |
| `EMBED_DIM` | `768` | Must match `EMBED_MODEL` |
| `LLM_PROVIDER`, `LLM_API_KEY` | | Unused until RAG picks an LLM; can stay empty |

`DATABASE_URL` overlaps with this backend's own `DB_*` fields (same
Supabase project), but they're read independently — set both. Never commit
real values: `.env` stays gitignored, and on Render they go in the service's
Environment tab (see `render.yaml`).

### What the backend gets back

```
from rag.pipeline import answer_query
answer_query("...") -> {"query": "...", "answer": "...",
                        "sources": [{"video_id": "<bronze video_id, a sha256
                                      hex string — not a UUID>",
                                     "video_url": "<public R2 URL> | null",
                                     "start_seconds": 0.0 | null,
                                     "end_seconds": 10.0 | null,
                                     "description": "..." | null,
                                     "event_name": "...", "camera_id": "...",
                                     "score": 0.83, ...}, ...]}
```

`rag_client.py` is the only file that knows this shape. It maps it onto
`RagQueryResult` (`app/schemas/rag.py`) so the frontend contract doesn't
move when RAG's does:

- `description` -> `caption`, falling back to `event_name` when null.
- `start_seconds` / `end_seconds` pass through and **can be null**.
- RAG returns at most 5 sources; `/search`'s `limit` can only trim that.
- Any exception from the package (Qdrant down, bad credentials, ...) ->
  **502** "Search service unreachable".
- Package not configured (`DATABASE_URL` missing) -> a mock answer in
  `ENVIRONMENT=development`, a **502** everywhere else — so a misconfigured
  deploy fails loudly instead of serving fake results.

The import is deferred to the first `/search` call on purpose: a missing
RAG env var must only break `/search`, not the whole API.

### Before it can answer anything: indexing

`/search` only queries the Qdrant collection; it never builds it. Run the
RAG repo's `scripts/index_events.py` **once** against the remote Qdrant (or
as a separate job) whenever bronze data changes — never on web server
startup. Until then `/search` answers "No matching footage was found".

### Upgrading to a new RAG version

1. The RAG team publishes a new tag (see the RAG repo's README).
2. Bump the tag in `requirements.txt` (e.g. `@v0.1.2` -> `@v0.1.3`) and run
   `pip install -r requirements.txt`.
3. Run the install check above, then `pytest tests/ -q`.
4. If the release changed `EMBED_MODEL`/`EMBED_DIM` or what gets indexed,
   the collection must be re-indexed **before** this bump is deployed, and
   the env vars updated to match.

### Troubleshooting

| Symptom | Cause |
|---|---|
| `pip install` fails with `Expected a Python module at: src/ilabs_cctv_rag/__init__.py` | The pinned tag predates the RAG repo's packaging fix (`module-name = "rag"` in its `pyproject.toml`). Pin a tag that includes it |
| `ModuleNotFoundError: No module named 'ilabs_cctv_rag'` | Import `rag`, not the distribution name |
| pip reports a `psycopg` conflict | The package needs `psycopg[binary]>=3.3.5`; keep this repo's pin at or above it |
| `/search` returns a `[mock — ...]` answer | `DATABASE_URL` isn't set in this process (development only) |
| `/search` returns 502 | Read the `detail`: usually a missing env var outside development, or Qdrant unreachable/wrong API key |
| First `/search` after a deploy takes ~30 s | fastembed downloads the model weights (~130 MB) on first use |
| Render restarts the service on the first `/search` | Out of memory: the embedding model needs ~700 MB, more than the free plan's 512 MB |

### Open questions with the RAG team

- Whether each result also includes `event_id`, not just `video_id` —
  without it, this backend can't reliably resolve which event in a
  multi-event video matched (see `app/services/bronze.py`'s four-hop bronze
  traversal).
- Already settled: RAG owns the full user-facing answer text; this
  backend's own LLM usage, if any, is scoped to the separate
  `/assistant/*` endpoints only, not `/search`.

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
    rag_client.py            Adapter over the in-process RAG package
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

# search (mocked in development until DATABASE_URL is set)
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
   the RAG package's vars (`DATABASE_URL`, `QDRANT_URL`, `QDRANT_API_KEY`,
   `LLM_PROVIDER`, `LLM_API_KEY`; the non-secret ones have defaults in
   `render.yaml`). `QDRANT_URL` must point at a remote Qdrant — left empty,
   the package uses an embedded `./qdrant_data` store that Render's
   ephemeral disk wipes on every deploy,
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
