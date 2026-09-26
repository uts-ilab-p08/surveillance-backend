"""
Turns bronze.geometries rows (per-frame detections) into the frontend's
per-object track shape for GET /videos/{video_id}/tracks (spec §4.4).
Pure database read — no RAG call.

Two things here were confirmed against real bronze data on 2026-09-26,
not guessed from the spec's own (incorrect) examples:
  - bounding_box_pixels is {x_min, y_min, x_max, y_max} corner pixels —
    neither of the two formats the spec's open question guessed at.
  - geometries.timestamp_seconds is already in the same time base as
    events.start_seconds/end_seconds (seconds into the source video
    file) — a real event's own geometries fall inside its own
    start/end window with no offset, so nothing is converted here.
"""
from collections import Counter, defaultdict

# The frontend's track "label" is coarse ("person"/"vehicle"), lowercase,
# per the spec's own example — not bronze's raw detector class ("car",
# "truck", ...) and not the capitalized ClipTag values app/services/
# tagging.py produces for a different purpose (Clip.tags). Same detector
# vocabulary, different output shape, so a separate small map rather than
# reusing tagging.py's dict directly.
_RAW_LABEL_TO_TRACK_LABEL = {
    "car": "vehicle",
    "truck": "vehicle",
    "bus": "vehicle",
    "motorcycle": "vehicle",
    "bicycle": "vehicle",
    "person": "person",
}

# Geometries land at roughly the detector's frame rate (~30/s in this
# dataset); the spec asks for at most ~10 boxes per second per object,
# first sample in each 0.1s bucket, and says the frontend interpolates
# between whatever is sent.
_BUCKETS_PER_SECOND = 10


def _normalize_box(bounding_box_pixels: dict, t: float, confidence: float | None) -> dict | None:
    try:
        x_min = bounding_box_pixels["x_min"]
        y_min = bounding_box_pixels["y_min"]
        x_max = bounding_box_pixels["x_max"]
        y_max = bounding_box_pixels["y_max"]
    except (KeyError, TypeError):
        return None
    return {
        "t": t,
        "x": x_min,
        "y": y_min,
        "w": x_max - x_min,
        "h": y_max - y_min,
        "confidence": confidence if confidence is not None else 0.0,
    }


def _object_label(raw_labels: list[str | None]) -> str:
    """One label per object (not per box) — the most common non-null raw
    label across this object's own geometry rows, mapped to the
    frontend's coarse vocabulary. Falls back to the raw value lowercased
    if it isn't one we have a mapping for, rather than dropping it."""
    counts = Counter(label for label in raw_labels if label)
    if not counts:
        return "unknown"
    raw = counts.most_common(1)[0][0].strip().lower()
    return _RAW_LABEL_TO_TRACK_LABEL.get(raw, raw)


def _downsample(boxes: list[dict]) -> list[dict]:
    kept = []
    last_bucket = None
    for box in boxes:
        bucket = int(box["t"] * _BUCKETS_PER_SECOND)
        if bucket != last_bucket:
            kept.append(box)
            last_bucket = bucket
    return kept


def build_tracks(rows: list[dict]) -> list[dict]:
    """rows: app.services.bronze.get_track_geometries() output — one row
    per (object_id, geometry), already ordered by (object_id, t)."""
    boxes_by_object: dict[str, list[dict]] = defaultdict(list)
    labels_by_object: dict[str, list[str | None]] = defaultdict(list)

    for row in rows:
        labels_by_object[row["object_id"]].append(row.get("label"))
        box = _normalize_box(row["bounding_box_pixels"], row["t"], row.get("confidence"))
        if box is not None:
            boxes_by_object[row["object_id"]].append(box)

    objects = []
    for object_id, boxes in boxes_by_object.items():
        objects.append(
            {
                "object_id": object_id,
                "label": _object_label(labels_by_object[object_id]),
                "boxes": _downsample(boxes),
            }
        )
    return objects
