"""
In-process client for the RAG package (vector search + LLM answer
generation), replacing the earlier plan of calling a separate RAG
service over HTTP — see the RAG repo's README, "Using it from the
backend": `from rag.pipeline import answer_query`.

The package's answer_query() deliberately returns only
{score, annotation, video_id, event_id} per source — camera, scene,
timestamps and video_url are NOT included on purpose (see that repo's
pipeline.py: "the backend just does not need them: it looks the rest up
in Postgres by event_id"). This module does exactly that lookup, reusing
app/services/bronze.py the same way app/services/clip_builder.py already
does for GET /clips/{id}.
"""
from sqlalchemy.orm import Session

from app.schemas.rag import RagQueryResult, RagResultItem
from app.services import bronze


class RagServiceUnavailable(Exception):
    pass


def _load_answer_query():
    # Imported lazily (not at module load time) so that a process which
    # never calls /search doesn't pay the RAG package's import cost
    # (embedding model, Qdrant client) at startup, and so that a missing
    # or invalid env var surfaces here as RagServiceUnavailable rather
    # than crashing the whole app at boot.
    from rag.pipeline import answer_query

    return answer_query


def _to_item(db: Session, source: dict) -> RagResultItem | None:
    """One RAG source -> one RagResultItem, enriched from bronze.

    Returns None if the event_id RAG returned no longer exists in bronze
    (index/DB drift) — the caller drops those rather than returning a
    result the frontend can't play.
    """
    event = bronze.get_event_with_video(db, source["event_id"])
    if event is None:
        return None

    return RagResultItem(
        video_id=event["video_id"],
        video_url=event["video_url"],
        start_seconds=event["start_seconds"] or 0.0,
        end_seconds=event["end_seconds"] or 0.0,
        # RAG's "annotation" is bronze's events.description under a
        # different name (see that repo's README) — prefer it, since
        # it's exactly what was embedded/matched against, but fall back
        # to a fresh bronze read if it's ever missing.
        caption=source.get("annotation") or event["description"] or event["event_name"] or "",
        score=source["score"],
    )


def query(db: Session, query_text: str, limit: int = 10) -> RagQueryResult:
    try:
        answer_query = _load_answer_query()
    except Exception as exc:  # package missing, or a required env var absent
        raise RagServiceUnavailable(f"RAG package not available: {exc}") from exc

    try:
        response = answer_query(query_text)
    except Exception as exc:  # Qdrant unreachable, embedding failure, etc.
        raise RagServiceUnavailable(str(exc)) from exc

    items = [_to_item(db, s) for s in response["sources"][:limit]]
    return RagQueryResult(
        answer=response["answer"],
        results=[item for item in items if item is not None],
    )
