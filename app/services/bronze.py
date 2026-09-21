"""
Read-only queries against the annotation team's bronze schema (see the
Annotations Dictionary — bronze.videos -> bronze.events, and
bronze.objects -> bronze.event_objects -> bronze.events for the object
side; videos and objects are never joined directly).

This backend does not own bronze and must not write to it. Credentials
should eventually be a read-only role scoped to `bronze` (see README —
still using the same full-access connection as everything else for now).
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

# One event plus its parent video's fields, exactly the join Appendix A.1
# of the Annotations Dictionary demonstrates.
_EVENT_WITH_VIDEO_SQL = text("""
    SELECT
        e.event_id, e.event_name, e.description,
        e.start_seconds, e.end_seconds,
        v.video_id, v.camera_id, v.scene, v.capture_start_local, v.video_url
    FROM bronze.events e
    JOIN bronze.videos v ON v.video_id = e.video_id
    WHERE e.event_id = :event_id
""")

# Object labels for one event, via the only path that exists
# (events -> event_objects -> objects; see §2.1/§2.4 of the dictionary).
_OBJECT_TYPES_FOR_EVENT_SQL = text("""
    SELECT DISTINCT jsonb_object_keys(o.label_details) AS label
    FROM bronze.event_objects eo
    JOIN bronze.objects o ON o.object_id = eo.object_id
    WHERE eo.event_id = :event_id
""")

# Nearby events on the same camera, closest in time first — our own
# "relatedness" heuristic (the frontend spec leaves this to the API layer).
# Four-hop join (videos -> events -> event_objects -> objects) is not
# needed here since we're going video -> events, not through objects.
_RELATED_EVENTS_SQL = text("""
    SELECT
        e.event_id, e.event_name, e.description,
        e.start_seconds, e.end_seconds,
        v.video_id, v.camera_id, v.scene, v.capture_start_local, v.video_url
    FROM bronze.events e
    JOIN bronze.videos v ON v.video_id = e.video_id
    WHERE v.camera_id = :camera_id AND e.event_id != :event_id
    ORDER BY ABS(EXTRACT(EPOCH FROM (v.capture_start_local - :anchor_capture_start))
                 + e.start_seconds - :anchor_start_seconds)
    LIMIT :limit
""")

# Average per-frame detection confidence across an event's objects, used
# only as a stand-in "confidence" when a clip is fetched directly by id
# rather than returned from a RAG search (which has its own relevance
# score — see app/services/clip_builder.py for why this distinction
# matters).
_AVG_DETECTION_CONFIDENCE_SQL = text("""
    SELECT AVG(g.confidence) AS avg_confidence
    FROM bronze.event_objects eo
    JOIN bronze.geometries g ON g.object_id = eo.object_id
    WHERE eo.event_id = :event_id
""")


def get_event_with_video(db: Session, event_id: str) -> dict | None:
    row = db.execute(_EVENT_WITH_VIDEO_SQL, {"event_id": event_id}).mappings().first()
    return dict(row) if row else None


def get_object_types_for_event(db: Session, event_id: str) -> list[str]:
    rows = db.execute(_OBJECT_TYPES_FOR_EVENT_SQL, {"event_id": event_id}).all()
    return [row[0] for row in rows]


def get_avg_detection_confidence(db: Session, event_id: str) -> float | None:
    value = db.execute(_AVG_DETECTION_CONFIDENCE_SQL, {"event_id": event_id}).scalar()
    return float(value) if value is not None else None


def get_related_events(db: Session, event: dict, limit: int = 4) -> list[dict]:
    rows = db.execute(
        _RELATED_EVENTS_SQL,
        {
            "event_id": event["event_id"],
            "camera_id": event["camera_id"],
            "anchor_capture_start": event["capture_start_local"],
            "anchor_start_seconds": event["start_seconds"] or 0,
            "limit": limit,
        },
    ).mappings().all()
    return [dict(row) for row in rows]

_CAMERA_DIRECTORY_SQL = text("""
    SELECT
        v.camera_id AS code,
        MAX(v.scene) AS perspective,
        COUNT(DISTINCT e.event_id) AS event_count
    FROM bronze.videos v
    LEFT JOIN bronze.events e ON e.video_id = v.video_id
    WHERE v.camera_id IS NOT NULL
    GROUP BY v.camera_id
    ORDER BY v.camera_id
""")


def get_camera_directory(db: Session) -> list[dict]:
    rows = db.execute(_CAMERA_DIRECTORY_SQL).mappings().all()
    return [dict(row) for row in rows]

