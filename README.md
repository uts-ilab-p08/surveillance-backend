# Surveillance Video Query API — Backend

FastAPI backend for the "Interactive Surveillance Video Querying Using LLMs
and Multi-Camera CCTV Datasets" project.

**Scope: this repo is box 3 only ("Backend — FastAPI") — Auth, Core
Endpoints, Postgres, and Video File Storage.** Box 1 (annotation pipeline)
and box 2 (RAG: vector DB + LLM) are separate repos owned by teammates.
This backend never runs a vision-language model, never talks to a vector
store, and never calls an LLM SDK directly — it owns user/video metadata
and orchestrates the other two boxes over HTTP.

## Decisions made so far

| Area | Choice | Why |
|---|---|---|
| Inter-repo integration | **HTTP calls to each sibling service**, contracts documented in `app/services/annotation_client.py` and `app/services/rag_client.py` | You confirmed each box is its own repo/deployable. Backend calls out; it doesn't share a DB or poll for work. |
| Annotation pipeline hookup | `POST /annotate` → `POST {ANNOTATION_SERVICE_URL}/jobs`, pipeline reports back via `POST /videos/{id}/annotation-callback` (shared-secret header, not user auth) | Annotation is slow/async, so a fire-and-forget job + callback fits better than the backend blocking or polling. |
| RAG hookup | `GET /search` → `POST {RAG_SERVICE_URL}/query`, response passed straight through | Backend does no retrieval or generation itself; it's a thin authenticated proxy in front of the RAG service. |
| DB access | **SQLAlchemy 2.0 + Alembic migrations** | Assumed (you hadn't specified) — the standard, portable choice for a direct Postgres/Supabase connection. |
| Auth | **HTTP Basic Auth**, bcrypt-hashed passwords, one `get_current_user` dependency protecting every user-facing route | Matches the diagram. Upgrade path to JWT/sessions documented in `app/api/deps.py` as a one-function change. |
| Backend's own DB schema | `users`, `cameras`, `videos` (+ `annotation_status`/`annotation_error`), `saved_queries` — **no captions, no embeddings** | Those live in the RAG repo's own store. Keeping them out of this schema means this repo never has to track that service's embedding model/dimension. |

Both sibling-service clients work with **no service configured**
(`ANNOTATION_SERVICE_URL` / `RAG_SERVICE_URL` unset): `/annotate` accepts
the job and just logs instead of calling out (status sits at
`processing` until you flip it via the callback endpoint, e.g. with curl),
and `/search` returns a canned mock answer. That means this repo is fully
runnable and testable on its own before either sibling repo exists —
swap the URLs in when they're deployed, no code changes needed.

## What's added beyond the diagram (and why)

- **`POST /videos/upload`** — the diagram's stage 1 starts from "unannotated
  clips from storage / new uploads," but nothing produces that storage
  entry. This endpoint saves the file (local disk in dev, Supabase Storage
  in prod — `app/services/storage.py`) and creates the `Video` row that
  `/annotate` and the pipeline service operate on.
- **`POST /videos/{id}/annotation-callback`** — the pipeline service's way
  of reporting a job finished. Guarded by a shared-secret header
  (`ANNOTATION_CALLBACK_SECRET`), not user Basic Auth, since it's
  machine-to-machine.
- **`GET /health`** — unauthenticated liveness check for deploy
  platforms/uptime monitors.
- **`annotation_status` / `annotation_error` on `Video`** — since
  annotation is async now, the frontend needs somewhere to poll.
- `/api/v1` prefix on every route except `/health`.

Deliberately **not** built yet: rate limiting, request logging middleware,
retry/backoff on the sibling-service calls, pagination cursors beyond
offset/limit, and the "Plus" box (saved-queries CRUD, exports, roles) — the
`SavedQuery` table exists so that's a routes-only addition later.

## Contracts with the other two repos

Share these with the annotation-pipeline and RAG teammates — they're the
only coupling between the repos.

**Annotation pipeline** (`app/services/annotation_client.py`):
```
Backend  -> POST {ANNOTATION_SERVICE_URL}/jobs
            {"video_id": "...", "video_url": "<reachable storage URL>",
             "callback_url": "<this backend's callback URL>"}
Pipeline -> POST {callback_url}
            {"status": "done" | "failed", "error": "..." (if failed)}
            header: X-Callback-Secret: <ANNOTATION_CALLBACK_SECRET>
```

**RAG service** (`app/services/rag_client.py`):
```
Backend -> POST {RAG_SERVICE_URL}/query
           {"query": "...", "limit": 10}
RAG     -> 200 {"answer": "...",
                "results": [{"video_id": "...", "start_seconds": 0.0,
                              "end_seconds": 10.0, "caption": "...",
                              "score": 0.83}, ...]}
```

These are proposals, not settled — confirm the exact shape with each team
before they build against it; the schemas live in `app/schemas/video.py`
(`AnnotationCallback`) and `app/schemas/rag.py` (`RagQueryResult`) so
changes are a one-file diff.

## On "should an agent orchestrate this?"

Not for this repo. It's a thin, synchronous FastAPI service that owns
metadata/storage and makes two outbound HTTP calls — a job-orchestration or
agent framework would add moving parts without solving a problem this repo
has. If anything in this project wants an agentic pattern, it's inside the
RAG repo's query-understanding step (turning "did anyone enter after the
red car arrived" into structured filters) — that's their concern, not
something wrapped around this backend.

## Project layout

```
app/
  main.py              FastAPI app, router wiring, CORS
  core/
    config.py          Settings (env vars / .env)
    security.py        Password hashing
  db/
    models.py           SQLAlchemy models (User, Camera, Video, SavedQuery)
    session.py           Engine + per-request session dependency
  schemas/               Pydantic request/response models (incl. rag.py — the RAG contract)
  api/
    deps.py               Basic Auth dependency
    routes/               auth, videos, annotate (+callback), search, health
  services/
    annotation_client.py   HTTP client -> annotation-pipeline repo
    rag_client.py            HTTP client -> RAG repo
    storage.py                Video file storage abstraction (local/Supabase)
alembic/                  DB migrations
tests/
```

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# start local Postgres (no vector extension needed — that's the RAG repo's DB)
docker compose up -d

# generate + apply the initial migration
alembic revision --autogenerate -m "initial schema"
alembic upgrade head

uvicorn app.main:app --reload
```

Auth: create a user directly in the DB for now (no `/register` endpoint —
add one if self-serve signup is needed; the diagram doesn't call for it).

Endpoints live under `/api/v1` except `/health`. Interactive docs at
`/docs` once the app is running. `ANNOTATION_SERVICE_URL` and
`RAG_SERVICE_URL` can stay unset while developing standalone — see mock
behavior above.

## Next steps

1. Align the two HTTP contracts above with the annotation-pipeline and RAG
   teammates; adjust `app/schemas/video.py` / `app/schemas/rag.py` and the
   two client modules once they're confirmed.
2. Point `DATABASE_URL` at the real Supabase project.
3. Wire `SupabaseStorageBackend` in `app/services/storage.py` once a bucket
   exists, and make sure `video_url` sent to the annotation service is
   something that service can actually reach (signed URL, likely).
4. Set `ANNOTATION_SERVICE_URL` / `RAG_SERVICE_URL` / matching
   `ANNOTATION_CALLBACK_SECRET` once those services are deployed.
