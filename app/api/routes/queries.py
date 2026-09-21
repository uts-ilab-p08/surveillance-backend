from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db.models import RecentQuery, SavedQuery
from app.db.session import get_db
from app.schemas.clip import RecentQueryOut, SavedQueryCreate, SavedQueryOut

router = APIRouter(tags=["queries"])


@router.get("/queries/recent")
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
    return {"queries": queries}


@router.get("/queries/saved")
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
    return {"queries": queries}


@router.post("/queries/saved", status_code=status.HTTP_201_CREATED, response_model=SavedQueryOut)
def save_query(
    body: SavedQueryCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> SavedQueryOut:
    """§6 — the write side of Saved Queries. hits starts at 0: saving is a
    distinct action from running it (per the frontend spec); POST /search
    increments hits when it sees a matching saved query re-run."""
    row = SavedQuery(user_id=user.id, query_text=body.text, hits=0)
    db.add(row)
    db.commit()
    db.refresh(row)
    return SavedQueryOut(id=str(row.id), text=row.query_text, savedOn=row.created_at.isoformat(), hits=row.hits)
