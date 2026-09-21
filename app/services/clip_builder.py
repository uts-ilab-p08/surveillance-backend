"""
Turns a bronze event+video row into the frontend's Clip shape (see
app/schemas/clip.py and the frontend spec's §2.2 normalization table).

Two call sites, two different "confidence" meanings — both are passed
through raw, unscaled (no *100 conversion; team decision 2026-09-21 — if
the frontend wants a percentage it converts it itself):
  - From a RAG search result: confidence is RAG's own raw relevance score
    (0-1 float), passed in explicitly, unmodified.
  - From a direct GET /clips/{id} lookup (no search involved): there is
    no relevance score to report, so we fall back to the raw average
    object detection confidence for that event as a stand-in. This is a
    real design choice, not a RAG value — flagged in bronze.py too.
"""
from datetime import timedelta

from sqlalchemy.orm import Session

from app.schemas.clip import Clip
from app.services import bronze
from app.services.tagging import derive_tags


def _format_ts(capture_start_local, start_seconds: float | None) -> str:
    if capture_start_local is None or start_seconds is None:
        return "00:00:00"
    moment = capture_start_local + timedelta(seconds=start_seconds)
    return moment.strftime("%H:%M:%S")


def _format_date(capture_start_local) -> str:
    if capture_start_local is None:
        return ""
    # "Aug 4" style, no year — see the Annotations Dictionary's flagged
    # follow-up about multi-year date collisions once more footage lands.
    return f"{capture_start_local.strftime('%b')} {capture_start_local.day}"


def build_clip(
    db: Session,
    event: dict,
    *,
    order: int,
    confidence: float | None = None,
) -> Clip:
    """event: a row from bronze.get_event_with_video (or get_related_events).
    confidence: pass explicitly when building from a RAG result (RAG's raw
    0-1 score, unmodified); left None to fall back to the raw average
    detection confidence (see module docstring)."""
    object_types = bronze.get_object_types_for_event(db, event["event_id"])

    if confidence is None:
        avg_conf = bronze.get_avg_detection_confidence(db, event["event_id"])
        confidence = avg_conf if avg_conf is not None else 0.0

    return Clip(
        id=event["event_id"],
        camera=event["camera_id"] or "",
        code=event["camera_id"] or "",
        perspective=event["scene"] or "",
        ts=_format_ts(event["capture_start_local"], event["start_seconds"]),
        date=_format_date(event["capture_start_local"]),
        order=order,
        confidence=confidence,
        tags=derive_tags(object_types, event["event_name"]),
        objects=event["description"] or "",
        action=event["event_name"] or "",
        thumbnailUrl=None,  # not yet produced anywhere upstream — see README
        videoUrl=event["video_url"],
    )
