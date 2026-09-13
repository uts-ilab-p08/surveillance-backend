"""
HTTP client for the RAG repo (vector DB + LLM, a separate service/repo
owned by teammates). The backend never talks to a vector store or an LLM
SDK directly; it forwards the natural-language query and returns whatever
that service answers — this is the entire product's search path: user
query -> this endpoint -> RAG -> top-K videos with confidence scores ->
straight back to the frontend.

Contract with that service (align on this with the RAG team):
  Backend -> POST {RAG_SERVICE_URL}/query
             {"query": "...", "limit": 10}
  Service -> 200 {"answer": "...",
                  "results": [{"video_id": "<bronze video_id, a sha256 hex
                                string>", "video_url": "<public R2 URL, if
                                available>", "start_seconds": 0.0,
                                "end_seconds": 10.0, "caption": "...",
                                "score": 0.83}, ...]}

If RAG_SERVICE_URL isn't set (e.g. local dev before that repo/service
exists), query() returns a canned mock response so GET /search is
exercisable end-to-end before the sibling service is up.
"""
import httpx

from app.core.config import get_settings
from app.schemas.rag import RagQueryResult


class RagServiceUnavailable(Exception):
    pass


def query(query_text: str, limit: int = 10) -> RagQueryResult:
    settings = get_settings()

    if not settings.rag_service_url:
        return RagQueryResult(
            answer=f"[mock — RAG_SERVICE_URL not configured] No live search backend for: {query_text!r}",
            results=[],
        )

    try:
        resp = httpx.post(
            f"{settings.rag_service_url}/query",
            json={"query": query_text, "limit": limit},
            timeout=30.0,
        )
        resp.raise_for_status()
        return RagQueryResult.model_validate(resp.json())
    except httpx.HTTPError as exc:
        raise RagServiceUnavailable(str(exc)) from exc
