"""
In-process client for the RAG package (vector search + LLM answer
generation), replacing the earlier plan of calling a separate RAG
service over HTTP — see the RAG repo's README, "Using it from the
backend": `from rag.pipeline import answer_query`.

The package's answer_query() deliberately returns only
{score, annotation, video_id, event_id} per source — camera, scene,
timestamps and video_url are NOT included on purpose (see that repo's
pipeline.py: "the backend just does not need them: it looks the rest up
in Postgres by event_id"). This module does exactly that lookup and the
rest of the frontend spec's §5.1 enrichment, reusing app/services/
bronze.py and app/services/tagging.py the same way app/services/
clip_builder.py already does for GET /clips/{id}.

Filtering (cameras/scenes/tags/min_confidence/date range) is applied
HERE, after enrichment — not inside RAG's own retrieval. The spec's
preference is filtering "before retrieval, not after" for exactly the
reason this violates: RAG's answer_query() only ever returns up to 5
sources (its own TOP_K), so filtering afterward can only narrow those 5
— it can never surface a 6th matching result RAG didn't return. True
pre-retrieval filtering would need RAG's own answer_query() to accept
structured filter parameters, which it doesn't today (its NL filter
extractor only recognises camera/scene mentioned in the query text
itself, and has no concept of tags, a confidence threshold, or a date
range at all — see that repo's filters.py). This is a real, known
limitation, not an oversight — raise it with the RAG team if a demo
needs it to behave differently.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.schemas.rag import RagQueryResult, RagResultItem
from app.services import bronze, tagging


class RagServiceUnavailable(Exception):
    pass


@dataclass
class SearchFilters:
    """The optional query params frontend spec §5.1 adds to GET /search."""

    cameras: list[str] | None = None
    scenes: list[str] | None = None
    tags: list[str] | None = None
    min_confidence: int | None = None  # 0-100, matches score * 100
    date_from: date | None = None
    date_to: date | None = None

    @property
    def is_empty(self) -> bool:
        return not any(
            [self.cameras, self.scenes, self.tags, self.min_confidence, self.date_from, self.date_to]
        )


def _load_answer_query():
    # Imported lazily (not at module load time) so that a process which
    # never calls /search doesn't pay the RAG package's import cost
    # (embedding model, Qdrant client) at startup, and so that a missing
    # or invalid env var surfaces here as RagServiceUnavailable rather
    # than crashing the whole app at boot.
    from rag.pipeline import answer_query

    return answer_query


def _build_timestamp(capture_start_local, start_seconds: float | None, capture_time_zone: str | None) -> str | None:
    """ISO 8601 with a real UTC offset, or None if we can't honestly
    produce one. capture_time_zone in bronze has been seen as literally
    "unknown" for at least some videos (confirmed 2026-09-26) — rather
    than guess a timezone (which would silently mislabel every
    timestamp), this returns None whenever the value isn't a real,
    parseable IANA zone name. None here is the honest answer, not a bug;
    see the frontend spec §5.1's own "or null with a documented reason"."""
    if capture_start_local is None or start_seconds is None or not capture_time_zone:
        return None
    try:
        tz = ZoneInfo(capture_time_zone)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    naive = capture_start_local + timedelta(seconds=start_seconds)
    return naive.replace(tzinfo=tz).isoformat()


def _to_item(db: Session, source: dict) -> RagResultItem | None:
    """One RAG source -> one RagResultItem, enriched from bronze.

    Returns None if the event_id RAG returned no longer exists in bronze
    (index/DB drift) — the caller drops those rather than returning a
    result the frontend can't play.
    """
    event = bronze.get_event_with_video(db, source["event_id"])
    if event is None:
        return None

    object_types = bronze.get_object_types_for_event(db, event["event_id"])

    return RagResultItem(
        video_id=event["video_id"],
        # NOT yet a signed, browser-playable URL — see app/schemas/rag.py.
        video_url=event["video_url"],
        start_seconds=event["start_seconds"] or 0.0,
        end_seconds=event["end_seconds"] or 0.0,
        # RAG's "annotation" is bronze's events.description under a
        # different name (see that repo's README) — prefer it, since
        # it's exactly what was embedded/matched against, but fall back
        # to a fresh bronze read if it's ever missing.
        caption=source.get("annotation") or event["description"] or event["event_name"] or "",
        score=source["score"],
        event_id=event["event_id"],
        event_name=event["event_name"],
        description=event["description"],
        camera=event["camera_id"],
        scene=event["scene"],
        timestamp=_build_timestamp(event["capture_start_local"], event["start_seconds"], event.get("capture_time_zone")),
        tags=tagging.derive_tags(object_types, event["event_name"]),
    )


def _passes_filters(item: RagResultItem, filters: SearchFilters) -> bool:
    if filters.cameras and item.camera not in filters.cameras:
        return False
    if filters.scenes and item.scene not in filters.scenes:
        return False
    if filters.tags and not any(tag in filters.tags for tag in item.tags):
        return False
    if filters.min_confidence is not None and (item.score * 100) < filters.min_confidence:
        return False
    if filters.date_from or filters.date_to:
        if not item.timestamp:
            # Can't confirm a date match without a real timestamp (see
            # _build_timestamp's "unknown timezone" case) — exclude
            # rather than guess it matches.
            return False
        item_date = datetime.fromisoformat(item.timestamp).date()
        if filters.date_from and item_date < filters.date_from:
            return False
        if filters.date_to and item_date > filters.date_to:
            return False
    return True


def query(
    db: Session,
    query_text: str,
    limit: int = 10,
    filters: SearchFilters | None = None,
) -> RagQueryResult:
    filters = filters or SearchFilters()

    try:
        answer_query = _load_answer_query()
    except Exception as exc:  # package missing, or a required env var absent
        raise RagServiceUnavailable(f"RAG package not available: {exc}") from exc

    try:
        response = answer_query(query_text)
    except Exception as exc:  # Qdrant unreachable, embedding failure, etc.
        raise RagServiceUnavailable(str(exc)) from exc

    items = [_to_item(db, s) for s in response["sources"][:limit]]
    items = [item for item in items if item is not None]

    if not filters.is_empty:
        items = [item for item in items if _passes_filters(item, filters)]

    return RagQueryResult(answer=response["answer"], results=items)
