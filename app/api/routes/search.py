from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import CurrentUser, get_current_user
from app.schemas.rag import RagQueryResult
from app.services.rag_client import RagServiceUnavailable, query as rag_query

router = APIRouter(tags=["search"])


@router.get("/search", response_model=RagQueryResult)
def search(
    q: str,
    limit: int = 10,
    _user: CurrentUser = Depends(get_current_user),
) -> RagQueryResult:
    """
    Diagram's "GET /search — NL query via RAG". The backend does no
    retrieval or LLM work itself — it hands the query to the RAG package
    (vector search + answer generation live there, see
    app/services/rag_client.py) and returns its answer.
    """
    try:
        return rag_query(q, limit=limit)
    except RagServiceUnavailable as exc:
        raise HTTPException(status_code=502, detail=f"Search service unreachable: {exc}") from exc
