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
- **RAG repo** — vector search + LLM, shipped as an installable Python
  package (`ilabs-cctv-rag`, pinned in `requirements.txt`) rather than a
  separate HTTP service. `GET /search` calls `rag.pipeline.answer_query()`
  in-process to get each result's `video_id`/`event_id`/score, then this
  repo does its own lookup into `bronze` (see "Talking to the RAG repo")
  to fill in everything the frontend needs beyond that. The assistant
  endpoints (`POST /assistant/ask`, `/assistant/ask/stream`,
  `/assistant/suggestions`) reuse the same package's `rag.llm.complete()`
  for text generation, rather than calling an LLM directly.
- **This repo** — the search relay plus enrichment, the conversational
  assistant endpoints, and a read layer over `bronze` for clip/camera
  detail and per-user query history.
- **Supabase Auth (GoTrue)** — owns identity entirely. The frontend logs
  users in directly against Supabase; this backend only verifies the JWT
  it issues.

Because the RAG package runs in-process, there's no separate service to
start or point a URL at — `LLM_BASE_URL`/`LLM_MODEL`/`LLM_API_KEY` and
`QDRANT_URL`/`QDRANT_API_KEY` (see "Deploying to Render" below) are read
by the package itself, not by this repo's own `Settings`. There's no
mock fallback built into *this* repo for that path anymore (the old
`RAG_SERVICE_URL`-unset mock was removed along with the HTTP client) —
what happens with those vars unset is the RAG package's own behavior, not
something this README can promise; check with Abhishek/that repo if you
need to develop against `/search` without real values configured.

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
| `GET /search` | Calls the RAG package in-process (`rag.pipeline.answer_query()`), then enriches each result from `bronze` by `event_id` — `event_name`, `description`, `camera`, `scene`, `tags`, `thumbnail_url`, and an honest-`null` `timestamp` (see "Talking to the RAG repo"). Also accepts `cameras`/`scenes`/`tags`/`min_confidence`/`date_from`/`date_to`, applied **after** retrieval, not before — a documented limitation, not an oversight (see that section). Writes the `recent_queries`/`saved_queries.hits` side effects described in the frontend spec. |
| `GET /clips/{id}` | Fetches one clip directly from the annotation team's `bronze` schema, by `event_id`. Independent of the RAG contract — built and working. |
| `GET /clips/{id}/related` | Nearby clips on the same camera, closest in time first (our own heuristic — the frontend contract doesn't specify one). |
| `GET /clips/{id}/thumbnail.jpg` | One JPEG frame for the clip — the moment with the most detected objects, via `bronze.geometries`. Extracted in-process with PyAV on first request (~1-6s, network-bound against R2) and cached on local disk after that. **Public** (no Bearer token): it's loaded via `<img src>`, and the source MP4 is already public. `GET /search` fills each result's `thumbnail_url` with this route. |
| `GET /videos/{video_id}/tracks` | Per-object bounding-box tracks for one video, from `bronze.geometries`. `start_seconds`/`end_seconds` are validated against the video's real `duration_seconds` (422 if out of range) rather than trusted blindly. |
| `GET /cameras` | The Cameras Directory modal — camera code, scene, and event count, aggregated from `bronze`. |
| `GET /queries/recent?limit=N` | A user's recent searches, newest first. `limit` defaults to 20 (1-100); Home asks for 3. Rows are written as a side effect of `GET /search`. |
| `GET /queries/saved` | A user's bookmarked searches. |
| `POST /queries/saved` | Bookmarks a query. Idempotent — re-bookmarking the same (normalized) query text returns the existing row rather than creating a duplicate, and `hits` increments as a side effect of `GET /search` matching that text. |
| `DELETE /queries/saved/{id}` | Un-bookmarks a query. 404 (not 403) for a saved query that doesn't belong to the caller, so it can't be used to probe which ids exist. |
| `POST /assistant/ask` | Conversational follow-up chat on Results/Clip Detail. Stateless — the frontend resends `query`/`moments`/`history`/`scope`/`focus_moment_id` every call; this backend never stores chat state. Answers only from the given `moments` via `rag.llm.complete()`, with the model's own citations checked against the request before being returned (see `app/services/assistant.py`). |
| `POST /assistant/ask/stream` | Same request/response contract as `POST /assistant/ask`, as Server-Sent Events, so the frontend can show what's happening (`Reading N moments…`, `Generating answer…`, `Verifying citations…`, `Drafting follow-up questions…`) instead of a blank wait. Coarse status only, not token streaming — `rag.llm.complete()` has no streaming variant, so there's a real gap between the "generating" status and the result. Needs a `fetch()` + manual SSE reader on the frontend, not `new EventSource(...)` (that API can't send the request body this needs). |
| `POST /assistant/suggestions` | Opening suggested questions for Results/Clip Detail — rule-based, not an LLM call, so it can guarantee it never suggests a question the data can't answer. Prefers real `bronze` annotations (object detections, average confidence) via each moment's `event_id` over pattern-matching the caption text; a moment with no `event_id` falls back to the caption heuristic. |
| `GET /health` | Plain liveness check, no auth — for uptime monitors and deploy platforms. |

All routes above except `/health` require a valid Supabase JWT.

**`score`/`confidence` is RAG's raw relevance score, unscaled (0–1 float), passed
through exactly as received — no `* 100` conversion.** If the frontend
wants a percentage, it converts it client-side. Note this diverges from
the frontend spec's own written §2.2 normalization table
(`confidence = round(score * 100)`) — flagged to the frontend team,
pending their sign-off on updating that doc to match.

Not built yet, on purpose: rate limiting and request logging middleware.
Video upload and the annotation hand-off (`POST /videos/upload`, `POST
/annotate`) stay removed — the product doesn't let users upload footage.

## Talking to the RAG repo

This one contract is the entire coupling between this repo and the RAG
repo — and it's also the whole product's search path (user query -> this
call -> bronze enrichment -> frontend). If the package's own return shape
changes, it's a conversation with Abhishek first.

**RAG package** (`app/services/rag_client.py`), in-process, not HTTP:
```python
from rag.pipeline import answer_query
answer_query(query, limit=10)  # -> {score, annotation, video_id, event_id} per source
```
Deliberately minimal on purpose — camera, scene, timestamps and
`video_url` are **not** included; the package's own view is "the backend
just does not need them: it looks the rest up in Postgres by `event_id`".
`app/services/rag_client.py` does exactly that lookup (via
`app/services/bronze.py` and `app/services/tagging.py`) to build the
`RagResultItem` the frontend actually gets — see `app/schemas/rag.py`.

Two things worth knowing if this needs to change:

- **Filtering is applied after retrieval, not before.** `answer_query()`
  only ever returns up to its own top-K (5) sources, and has no
  structured-filter parameter — its own NL filter extractor only
  recognises a camera/scene mentioned in the query text itself, nothing
  about tags, a confidence threshold, or a date range. So `/search`'s
  `cameras`/`scenes`/`tags`/`min_confidence`/`date_from`/`date_to`
  params can only narrow those 5 results, never surface a 6th one RAG
  didn't return. Real pre-retrieval filtering needs the RAG package's own
  `answer_query()` to accept filters — raise it with that team if a demo
  needs it to behave differently.
- **`timestamp` is `null` for effectively every result today.** It's
  meant to be a real calendar date/time (not the in-clip
  `start_seconds`/`end_seconds`, which are always populated), but
  `bronze.videos.capture_time_zone` is `'unknown'` for every video in the
  dataset right now — confirmed by direct query, not assumed — so there's
  no safe way to convert seconds-into-file into a real UTC/local time.
  `date_from`/`date_to` filtering correctly excludes (never assumes) a
  result with no real timestamp, rather than guessing. This is an
  annotation-pipeline-level gap, not something this backend can fix on
  its own.

RAG owns the full user-facing answer text for `/search`; this backend's
own LLM usage (`rag.llm.complete()`, the same package's shared client) is
scoped to the separate `/assistant/*` endpoints only.

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
    routes/               search, clips, cameras, queries, assistant, videos, health
  services/
    rag_client.py            In-process client for the RAG package
                              (rag.pipeline.answer_query) + bronze
                              enrichment for GET /search
    assistant.py              POST /assistant/ask(/stream) and
                              /assistant/suggestions — prompt-building,
                              citation parsing, rule-based suggestions
    thumbnails.py             Lazy per-clip JPEG frame extraction (PyAV),
                              cached on local disk
    tracks.py                 Per-object bounding-box tracks for
                              GET /videos/{video_id}/tracks
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

This runs every route against a real Postgres instance. `/search` and
the `/assistant/*` endpoints also need the RAG package's own env vars
set for real (`LLM_BASE_URL`/`LLM_MODEL`/`LLM_API_KEY`,
`QDRANT_URL`/`QDRANT_API_KEY` — see "Deploying to Render") — there's no
mock fallback for those anymore:

```bash
alembic upgrade head
uvicorn app.main:app --reload &

TOKEN="<a Supabase-issued JWT, e.g. from logging in on the frontend>"
AUTH="-H \"Authorization: Bearer $TOKEN\""

# search
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/search?q=did+anyone+enter+the+building"

# unit tests
pytest tests/ -q
```

## Deploying to Railway

The backend is deployed on **Railway** (Hobby plan). Render's free tier
isn't enough: the RAG package loads its `BAAI/bge-base-en-v1.5`
embedding model in-process on the first `/search`, which alone peaks at
~800MB RAM (measured in a Linux container). Render's 512MB instance gets
OOM-killed and `/search` never responds. Budget roughly 1GB for this
service, which works out to about $11-12/month in Railway usage at its
posted rates ($10/GB RAM, $20/vCPU per month); the $5 Hobby fee counts
toward that.

`railway.json` holds the build/deploy config, and it **overrides the
dashboard**:

- `startCommand`: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
  Railpack can't infer this, because the app lives at `app/main.py`
  rather than a root `main.py`. Without `--host 0.0.0.0 --port $PORT`,
  uvicorn binds to `127.0.0.1:8000` and the platform never detects an
  open port.
- `preDeployCommand`: `alembic upgrade head`. It runs once per deploy,
  before the new version starts. If a migration fails, the deploy
  stops and the previous version keeps serving.
- `healthcheckPath`: `/health`. Traffic only switches over once it
  responds. It doesn't touch the embedding model, so it's fast.

One-time setup:

1. **New Project → Deploy from GitHub repo →** `surveillance-backend`
   (branch `main`).
2. **Settings → Networking → Generate Domain**, which gives
   `https://<name>.up.railway.app`.
3. **Variables**: the same values as your local `.env`:
   - `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME` (the shared
     Supabase project's pooler connection details).
   - `SUPABASE_URL` (the project's base URL; not a secret, see "Auth" above).
   - The RAG package's own vars: `DATABASE_URL`, `DB_SCHEMA`,
     `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION`, `EMBED_MODEL`,
     `EMBED_DIM`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` (see
     "Talking to the RAG repo"). `QDRANT_URL` must be real, or the
     package silently falls back to an empty local index.
   - `CORS_ORIGINS`: the frontend's deployed URL.
   - `ENVIRONMENT=production`.
   - **`PUBLIC_BASE_URL`** (see below).

   > **⚠️ `PUBLIC_BASE_URL`: don't skip it.** Set it to the domain from
   > step 2, with no trailing path: `https://<name>.up.railway.app`.
   > `GET /search` uses it to build each result's absolute
   > `thumbnail_url` (`<PUBLIC_BASE_URL>/api/v1/clips/{event_id}/thumbnail.jpg`).
   > If it's unset, the backend falls back to the incoming request's own
   > URL, which behind a proxy can come out as `http://`. An https
   > frontend then blocks every thumbnail as mixed content, and the cards
   > show broken images. Nothing errors server-side, so it's easy to miss.
   > Check after deploying: run a search and confirm `thumbnail_url` starts
   > with `https://`.

4. **Workspace → Usage**: set a **hard limit of $20** (the minimum
   allowed is $10) and a soft limit around $15. Railway emails at
   75/90/100% of the hard limit, then takes every service in the
   workspace offline. Nothing is deleted. Raise or remove the limit and
   it redeploys automatically.
5. Leave **Serverless** (app sleeping) **off** for demos. A woken service
   has to download and reload the ~800MB model, and Railway documents
   that the first request after sleeping may return 502.
6. `GET https://<name>.up.railway.app/health` should return
   `{"status": "ok"}`. Share the base URL with the frontend team.

Ephemeral disk: without a Volume, the thumbnail cache (`THUMBNAIL_CACHE_DIR`,
default is the OS temp dir) and fastembed's model download are both wiped
on every deploy/restart. The first `/search` then re-downloads the model
(~10s), and each thumbnail's first load is slow again (~1-6s). To keep
them, mount a Volume and point `THUMBNAIL_CACHE_DIR` and
`FASTEMBED_CACHE_PATH` at it. Setting `HF_TOKEN` silences the Hugging
Face "unauthenticated requests" warning and speeds up the download.

`render.yaml` is still in the repo. It works on a Render plan with ≥1GB
RAM, but it isn't the active deploy target.

## What's next

1. `GET /clips/{id}` and `/clips/{id}/related` still return `null` for
   `thumbnailUrl` — same route as `/search`, just not wired into
   `build_clip` yet.
2. `capture_time_zone` is `'unknown'` for every video in `bronze` today,
   so `/search`'s `timestamp` field is `null` everywhere (see "Talking to
   the RAG repo") — this needs a fix at the annotation-pipeline source,
   not here.
3. `/search`'s `cameras`/`scenes`/`tags`/`min_confidence`/`date_from`/
   `date_to` filtering is post-retrieval only, per RAG's own current
   `answer_query()` — worth raising with the RAG team if a demo needs
   true pre-retrieval filtering (see "Talking to the RAG repo").
4. Confirm the newer dependencies (`av`, `pillow`, for
   `GET /clips/{id}/thumbnail.jpg`) actually deploy cleanly on Render —
   not yet verified on a live deploy.
5. Double-check the Render dashboard actually has real values set for
   every `sync: false` var in `render.yaml` (RAG/Qdrant/LLM vars,
   `PUBLIC_BASE_URL`) — the YAML declares what Render should prompt for,
   it doesn't set the values itself.
6. Rate limiting and request logging middleware — not built yet, on
   purpose (see "Endpoints").
