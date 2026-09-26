from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db.models import RecentQuery, SavedQuery
from app.db.session import get_db
from app.schemas.rag import RagQueryResult
from app.services.rag_client import RagServiceUnavailable, SearchFilters, query as rag_query

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


@router.get("/search", response_model=RagQueryResult)
def search(
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

    _record_search_side_effects(db, user.id, q, result)
    return result
