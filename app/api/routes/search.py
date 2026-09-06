from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.db.models import User
from app.schemas.rag import RagQueryResult
from app.services.rag_client import RagServiceUnavailable, query as rag_query

router = APIRouter(tags=["search"])


@router.get("/search", response_model=RagQueryResult)
def search(
    q: str,
    limit: int = 10,
    _user: User = Depends(get_current_user),
) -> RagQueryResult:
    """
    Diagram's "GET /search — NL query via RAG". The backend does no
    retrieval or LLM work itself — it forwards the query to the RAG
    service (vector search + LLM answer generation live there) and passes
    the response straight through.
    """
    try:
        return rag_query(q, limit=limit)
    except RagServiceUnavailable as exc:
        raise HTTPException(status_code=502, detail=f"Search service unreachable: {exc}") from exc
