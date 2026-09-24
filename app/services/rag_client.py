"""
Adapter over the RAG package (ilabs-cctv-rag, imported as `rag`, owned by
teammates in uts-ilab-p08/iLabs-capstone-rag). The backend never talks to a
vector store or an LLM SDK directly; it hands the natural-language query to
`rag.pipeline.answer_query` and maps the answer onto this backend's own
RagQueryResult — this is the entire product's search path: user query ->
this endpoint -> RAG -> top-K moments with confidence scores -> straight
back to the frontend.

The package runs in-process (not as a separate service): it embeds the
query with fastembed and searches a remote Qdrant collection. It reads its
own settings straight from os.environ (DATABASE_URL, QDRANT_URL,
QDRANT_API_KEY, QDRANT_COLLECTION, EMBED_MODEL, ...) — not from
app.core.config — so those variables must be set in the process
environment (Render's Environment tab, or .env locally).

Package contract (rag.pipeline.answer_query, v0.1.0):
  answer_query("...") -> {"query": "...", "answer": "...",
                          "sources": [{"video_id": "<bronze video_id>",
                                       "video_url": "..." | None,
                                       "start_seconds": 0.0 | None,
                                       "end_seconds": 10.0 | None,
                                       "description": "..." | None,
                                       "event_name": "..." | None,
                                       "score": 0.83, ...}, ...]}
It always returns its own top-K (5), best first; `limit` here can only trim.

Import is deferred to the first query: `rag.config` raises KeyError at
import time when DATABASE_URL is unset, which must not take the whole API
down. In development that case returns a canned mock response so GET
/search stays exercisable without RAG access; in any other environment it's
an error, so a misconfigured deploy fails loudly instead of serving fakes.
"""
from typing import Callable

from app.core.config import get_settings
from app.schemas.rag import RagQueryResult, RagResultItem


class RagServiceUnavailable(Exception):
    pass


def _load_answer_query() -> Callable[[str], dict]:
    from rag.pipeline import answer_query

    return answer_query


def _to_item(source: dict) -> RagResultItem:
    return RagResultItem(
        video_id=source["video_id"],
        video_url=source.get("video_url"),
        start_seconds=source.get("start_seconds"),
        end_seconds=source.get("end_seconds"),
        caption=source.get("description") or source.get("event_name") or "",
        score=source["score"],
    )


def query(query_text: str, limit: int = 10) -> RagQueryResult:
    try:
        answer_query = _load_answer_query()
    except KeyError as exc:  # rag.config: a required env var is missing
        if get_settings().environment == "development":
            return RagQueryResult(
                answer=f"[mock — RAG package not configured, missing {exc}] No live search backend for: {query_text!r}",
                results=[],
            )
        raise RagServiceUnavailable(f"RAG package not configured: missing env var {exc}") from exc

    try:
        response = answer_query(query_text)
    except Exception as exc:  # Qdrant/embedding failures surface as the route's 502
        raise RagServiceUnavailable(str(exc)) from exc

    return RagQueryResult(
        answer=response["answer"],
        results=[_to_item(s) for s in response["sources"][:limit]],
    )
