from datetime import date
from typing import Generator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.core.config import get_settings
from app.db.models import RecentQuery, SavedQuery
from app.db.session import get_db
from app.schemas.rag import RagQueryResult
from app.services import thumbnails
from app.services.rag_client import RagServiceUnavailable, SearchFilters, query as rag_query
from app.services.sse import format_sse_event as _sse

router = APIRouter(tags=["search"])


def _record_search_side_effects(db: Session, user_id, query_text: str, result: RagQueryResult) -> None:
    """§5.1 "Also check the side effects": every /search call should log a
    recent-query row, and bump hits on a saved query with the same text.
    Not confirmed to be happening today (no code referenced RecentQuery/
    SavedQuery from this route before this change) — matches the spec's
    own suspicion from "0 matches last run" showing on every saved query.

    Best-effort: a failure here is logged-and-swallowed rather than
    turning a successful search into a 500 — the search result itself is
    already computed and correct either way.
    """
    try:
        camera_count = len({item.camera for item in result.results if item.camera})
        db.add(RecentQuery(user_id=user_id, query_text=query_text, camera_count=camera_count))

        normalized = query_text.strip().lower()
        saved = (
            db.query(SavedQuery)
            .filter(SavedQuery.user_id == user_id, func.lower(func.trim(SavedQuery.query_text)) == normalized)
            .first()
        )
        if saved is not None:
            saved.hits += 1

        db.commit()
    except Exception:
        db.rollback()


def _attach_thumbnail_urls(result: RagQueryResult, base_url: str) -> None:
    """Points each result at GET /clips/{event_id}/thumbnail.jpg. Only a
    URL — the frame itself is extracted when the browser first loads it,
    so search latency doesn't pay for frame extraction."""
    for item in result.results:
        if item.event_id:
            item.thumbnail_url = thumbnails.thumbnail_url(base_url, item.event_id)


@router.get("/search", response_model=RagQueryResult)
def search(
    request: Request,
    q: str,
    limit: int = 10,
    cameras: list[str] | None = Query(default=None),
    scenes: list[str] | None = Query(default=None),
    tags: list[str] | None = Query(default=None),
    min_confidence: int | None = Query(default=None, ge=0, le=100),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> RagQueryResult:
    """
    Diagram's "GET /search — NL query via RAG". Retrieval and answer
    generation happen in-process via the RAG package (rag.pipeline.
    answer_query); this backend enriches each result from bronze by
    event_id (see app/services/rag_client.py) before returning it.

    Filter params are applied AFTER retrieval/enrichment, not inside RAG
    — see app/services/rag_client.py's module docstring for why (RAG's
    own answer_query() has no filter parameters to pass these into).
    """
    filters = SearchFilters(
        cameras=cameras,
        scenes=scenes,
        tags=tags,
        min_confidence=min_confidence,
        date_from=date_from,
        date_to=date_to,
    )
    try:
        result = rag_query(db, q, limit=limit, filters=filters)
    except RagServiceUnavailable as exc:
        raise HTTPException(status_code=502, detail=f"Search service unreachable: {exc}") from exc

    _attach_thumbnail_urls(result, get_settings().public_base_url or str(request.base_url))
    _record_search_side_effects(db, user.id, q, result)
    return result


# ---------------------------------------------------------------------------
# GET /search/stream — same contract as GET /search, as Server-Sent
# Events, so the frontend can show what's happening instead of a blank
# wait on a slow query. Requested by Nelkit (frontend) — /search can be
# slow and leaves the user with no feedback while it runs.
#
# Unlike POST /assistant/ask/stream, this is a plain GET with no request
# body, so the frontend can use the browser's native EventSource directly
# — no manual fetch + stream-reader needed.
#
# Coarse status only, same honest limit as /assistant/ask/stream:
# rag_query() — retrieval, answer generation, bronze enrichment, AND the
# cameras/scenes/tags/min_confidence/date filtering — all happen inside
# that one call (see app/services/rag_client.py). There's no separate
# "retrieving" vs. "filtering" step visible at this route to report on
# individually; "searching" covers all of it as a single gap. The two
# steps after it (thumbnail URLs, recording search history) are real,
# separate, and fast, so they each get their own status.
# ---------------------------------------------------------------------------


def _search_stream_events(
    db: Session,
    user: CurrentUser,
    base_url: str,
    q: str,
    limit: int,
    filters: SearchFilters,
) -> Generator[str, None, None]:
    yield _sse("status", {"phase": "searching", "message": "Searching the video archive…"})
    try:
        result = rag_query(db, q, limit=limit, filters=filters)
    except RagServiceUnavailable as exc:
        yield _sse("error", {"message": f"Search service unreachable: {exc}"})
        return

    yield _sse("status", {"phase": "thumbnails", "message": "Preparing thumbnails…"})
    _attach_thumbnail_urls(result, base_url)

    yield _sse("status", {"phase": "saving", "message": "Saving to your search history…"})
    _record_search_side_effects(db, user.id, q, result)

    yield _sse("result", result.model_dump())


@router.get("/search/stream")
def search_stream(
    request: Request,
    q: str,
    limit: int = 10,
    cameras: list[str] | None = Query(default=None),
    scenes: list[str] | None = Query(default=None),
    tags: list[str] | None = Query(default=None),
    min_confidence: int | None = Query(default=None, ge=0, le=100),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    """Same params and same end result as GET /search — see that route's
    own docstring for the retrieval/enrichment/filtering contract. This
    one wraps it in SSE status events; event framing matches
    POST /assistant/ask/stream (`status` with {"phase","message"} while
    in progress, `result` once with the same shape GET /search returns,
    or `error` with {"message"} in place of result on an upstream
    failure)."""
    filters = SearchFilters(
        cameras=cameras,
        scenes=scenes,
        tags=tags,
        min_confidence=min_confidence,
        date_from=date_from,
        date_to=date_to,
    )
    base_url = get_settings().public_base_url or str(request.base_url)
    return StreamingResponse(
        _search_stream_events(db, user, base_url, q, limit, filters),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
