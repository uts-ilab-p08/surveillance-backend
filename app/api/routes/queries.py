import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db.models import RecentQuery, SavedQuery
from app.db.session import get_db
from app.schemas.clip import (
    RecentQueriesResponse,
    RecentQueryOut,
    SavedQueriesResponse,
    SavedQueryCreate,
    SavedQueryOut,
)

router = APIRouter(tags=["queries"])


@router.get("/queries/recent", response_model=RecentQueriesResponse)
def get_recent_queries(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """§3 — Home screen's "Recent Queries" list. Read-only here by design:
    rows are written only as a side effect of POST /search (once that's
    wired up to RAG — see README), never created directly."""
    rows = (
        db.query(RecentQuery)
        .filter(RecentQuery.user_id == user.id)
        .order_by(RecentQuery.created_at.desc())
        .limit(20)
        .all()
    )
    queries = [
        RecentQueryOut(id=str(row.id), text=row.query_text, ts=row.created_at.isoformat(), cameras=row.camera_count)
        for row in rows
    ]
    return RecentQueriesResponse(queries=queries)


@router.get("/queries/saved", response_model=SavedQueriesResponse)
def get_saved_queries(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """§6 — Saved Queries screen list."""
    rows = (
        db.query(SavedQuery)
        .filter(SavedQuery.user_id == user.id)
        .order_by(SavedQuery.created_at.desc())
        .all()
    )
    queries = [
        SavedQueryOut(id=str(row.id), text=row.query_text, savedOn=row.created_at.isoformat(), hits=row.hits)
        for row in rows
    ]
    return SavedQueriesResponse(queries=queries)


def _to_saved_query_out(row: SavedQuery) -> SavedQueryOut:
    return SavedQueryOut(id=str(row.id), text=row.query_text, savedOn=row.created_at.isoformat(), hits=row.hits)


def _find_existing_saved_query(db: Session, user_id, text: str) -> SavedQuery | None:
    normalized = text.strip().lower()
    return (
        db.query(SavedQuery)
        .filter(SavedQuery.user_id == user_id, func.lower(func.trim(SavedQuery.query_text)) == normalized)
        .first()
    )


@router.post("/queries/saved", status_code=status.HTTP_201_CREATED, response_model=SavedQueryOut)
def save_query(
    body: SavedQueryCreate,
    response: Response,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> SavedQueryOut:
    """§6 — the write side of Saved Queries. hits starts at 0: saving is a
    distinct action from running it (per the frontend spec); POST /search
    increments hits when it sees a matching saved query re-run.

    §5.3 — idempotent per (user, normalized text): re-saving the same
    query (case/whitespace-insensitive) returns the existing row with 200
    OK instead of creating a duplicate. A genuinely new query still
    returns 201 (the decorator's default)."""
    existing = _find_existing_saved_query(db, user.id, body.text)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return _to_saved_query_out(existing)

    row = SavedQuery(user_id=user.id, query_text=body.text, hits=0)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # Lost a race with a concurrent identical save — the unique index
        # (migration a1b2c3d4e5f6) caught what our own pre-check didn't.
        db.rollback()
        existing = _find_existing_saved_query(db, user.id, body.text)
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            return _to_saved_query_out(existing)
        raise
    db.refresh(row)
    return _to_saved_query_out(row)


@router.delete("/queries/saved/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_saved_query(
    id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    """§4.1 — the trash icon on Saved Queries. Hard delete: these are
    bookmarks, not evidence.

    404, never 403, for an id that doesn't belong to the caller — a 403
    would confirm the id exists at all (spec's explicit rule). This also
    means calling delete twice on the same id returns 404 the second
    time, which the frontend treats as "already gone" rather than an
    error.
    """
    try:
        query_id = uuid.UUID(id)
    except ValueError:
        # Not even a well-formed id: treat exactly like "not found" rather
        # than letting an invalid-uuid error hit the database (spec: 404,
        # never a different error, for an id that isn't the caller's).
        raise HTTPException(status_code=404, detail="Saved query not found")

    row = (
        db.query(SavedQuery)
        .filter(SavedQuery.id == query_id, SavedQuery.user_id == user.id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Saved query not found")

    db.delete(row)
    db.commit()
