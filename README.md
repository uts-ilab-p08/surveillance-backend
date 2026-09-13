# Surveillance Video Query API — Backend

This is the FastAPI service behind the Interactive Surveillance Video
Querying project. The product flow is: a user types a natural-language
query, this backend authenticates them and forwards the query to the RAG
service, and whatever RAG sends back — the top handful of matching video
clips, each with a confidence score — goes straight to the frontend. This
repo owns exactly two things: user auth, and that search relay.

It deliberately does **not** run a vision-language model, does not touch a
vector store, and does not call an LLM directly — that's the RAG team's
service. It also doesn't store or serve video files itself: raw footage,
annotations, and playback URLs live in the annotation team's `bronze`
schema and Cloudflare R2 bucket
([video-annotation-pipeline](https://github.com/uts-ilab-p08/video-annotation-pipeline.git),
metadata/CV pipeline in
[videometa](https://github.com/uts-ilab-p08/videometa.git)). There's no
user-facing video upload in this product yet, so this backend has no video
data of its own to manage.

## How it fits together

- **Annotation pipeline repo** — a set of notebooks (not yet a live
  service) that pull MEVA clips, run motion/object detection and a local
  VLM over them, and load the results into the shared Supabase project's
  `bronze` schema, with the actual video files hosted on Cloudflare R2.
- **RAG repo** — vector search + LLM. `GET /search` forwards the query
  there and passes the response straight back, including each result's
  video reference, playback URL, and confidence score. Retrieval and
  generation both happen on their side.
- **This repo** — auth and the one endpoint the frontend actually calls
  for search.

The RAG integration works without that service running: leave
`RAG_SERVICE_URL` unset and `/search` falls back to a mock response.
Useful for developing or demoing this repo on its own — flip the URL on
once RAG is up, no code changes needed.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# local Postgres (dev only — in staging/prod the DB_* fields in .env
# point at the shared Supabase project instead)
docker compose up -d

# first time only: apply migrations
alembic upgrade head

uvicorn app.main:app --reload
```

There's no `/register` endpoint yet, so add yourself a user straight in the
DB:

```bash
python -c "
from app.db.session import SessionLocal
from app.db.models import User
from app.core.security import hash_password
db = SessionLocal()
db.add(User(username='investigator1', hashed_password=hash_password('testpass123')))
db.commit()
"
```

Everything lives under `/api/v1` except `/health`. Swagger docs at `/docs`
once it's running.

**Heads up on a dependency quirk:** `passlib` (the password-hashing library)
hasn't been updated in a while and breaks on newer `bcrypt` releases —
`requirements.txt` pins `bcrypt==4.0.1` to work around it. If you ever see
`AttributeError: module 'bcrypt' has no attribute '__about__'` after
installing, something bumped bcrypt past that pin; reinstall with
`pip install "bcrypt==4.0.1"`.

## Endpoints

| Endpoint | What it does |
|---|---|
| `POST /auth/login` | Checks a username/password pair. Basic Auth on every request *is* the session, so this is mainly for the frontend to verify credentials once. |
| `GET /search` | Forwards a natural-language query to the RAG service and returns its answer plus the matching clips (video reference, playback URL, time range, caption, confidence score). |
| `GET /health` | Plain liveness check, no auth — for uptime monitors and deploy platforms. |

Auth is HTTP Basic for now, with bcrypt-hashed passwords at rest —
`app/api/deps.py` has a note on how to swap in JWT/sessions later without
touching route signatures.

Not built yet, on purpose: rate limiting, request logging middleware,
retry/backoff on the outbound RAG call, and the saved-queries endpoints
(the `SavedQuery` table exists for search history, the routes don't —
a quick follow-up whenever it's needed). Video upload and the annotation
hand-off (`POST /videos/upload`, `POST /annotate`) were removed — the
product doesn't let users upload footage right now, so there was nothing
for this backend to own there. If that changes later, both come back as a
scoped follow-up rather than dead code sitting around unused.

## Talking to the RAG repo

This one contract is the entire coupling between this repo and the RAG
repo — and it's also the whole product's search path (user query -> this
endpoint -> RAG -> top-K videos with confidence scores -> frontend). If
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
Worth confirming with both the RAG and annotation teams: `video_id` here
is whatever the annotation pipeline's `bronze.videos.video_id` is (a
SHA-256 hash of the original video name) — this backend doesn't mint its
own video IDs, so search results only make sense if RAG and the
annotation pipeline agree on that same identifier.

## Project layout

```
app/
  main.py              FastAPI app, router wiring, CORS
  core/
    config.py          Settings (env vars / .env)
    security.py        Password hashing
  db/
    models.py           SQLAlchemy models (User, SavedQuery)
    session.py           Engine + per-request session dependency
  schemas/               Pydantic request/response models (incl. rag.py — the RAG contract)
  api/
    deps.py               Basic Auth dependency
    routes/               auth, search, health
  services/
    rag_client.py            HTTP client -> RAG repo
alembic/                  DB migrations
tests/
```

## Testing it end-to-end

This runs every route against a real Postgres instance, with the RAG
service left on its built-in mock:

```bash
alembic upgrade head
uvicorn app.main:app --reload &

AUTH="-u investigator1:testpass123"   # after creating the user, see above

# login
curl -s $AUTH -X POST http://localhost:8000/api/v1/auth/login

# search (mocked until RAG_SERVICE_URL is set)
curl -s $AUTH "http://localhost:8000/api/v1/search?q=did+anyone+enter+the+building"

# unit tests
pytest tests/ -q
```

Auth correctly rejects a wrong password and no credentials; login and
search round-trip cleanly through a real database.

## What's next

1. Confirm the RAG contract above with the RAG team — update
   `app/schemas/rag.py` and `app/services/rag_client.py` once it's locked
   in, including how `video_id` lines up with the annotation pipeline's
   `bronze.videos.video_id`.
2. Set `RAG_SERVICE_URL` once that service is deployed (or reachable via a
   tunnel for local testing).
3. Decide whether saved-search history is in scope for this phase — if so,
   add the `POST/GET /saved-queries` routes against the existing
   `SavedQuery` table.
4. Deploy this API somewhere with a real, reachable URL (Render/Railway are
   reasonable free-tier options) so the frontend isn't stuck pointing at
   `localhost`.
