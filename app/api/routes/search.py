from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.rag import RagQueryResult
from app.services.rag_client import RagServiceUnavailable, query as rag_query

router = APIRouter(tags=["search"])


@router.get("/search", response_model=RagQueryResult)
def search(
    q: str,
    limit: int = 10,
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(get_current_user),
) -> RagQueryResult:
    """
    Diagram's "GET /search — NL query via RAG". Retrieval and answer
    generation happen in-process via the RAG package (rag.pipeline.
    answer_query); this backend enriches each result from bronze by
    event_id (see app/services/rag_client.py) before returning it.
    """
    try:
        return rag_query(db, q, limit=limit)
    except RagServiceUnavailable as exc:
        raise HTTPException(status_code=502, detail=f"Search service unreachable: {exc}") from exc
