"""
Maps bronze's open-vocabulary detector output onto the frontend's closed
ClipTag enum (Person, Vehicle, Entry, Exit, Loitering, Object Left).

Two independent sources feed a clip's tags:
  - object_types: raw YOLO detector labels from bronze.objects
    (label_details), e.g. "person", "car", "backpack", "chair" — dozens of
    possible values, most of which don't correspond to any ClipTag.
  - event_name: free-text from the annotation pipeline's VLM, e.g.
    "Vehicle enters", "Loitering near entrance" — not a controlled
    vocabulary, so this is a best-effort keyword match, not an exact one.

Per the frontend spec: if nothing maps cleanly, omit the tag rather than
inventing one. A clip can end up with more than one tag (e.g. both
"Vehicle" and "Entry"). This is a v1 heuristic, not a finished taxonomy —
expect to revisit the keyword lists once real (non-MEVA-sample) event_name
text is available from the annotation pipeline.
"""
from app.schemas.clip import ClipTag

# Direct, high-confidence mapping. Anything not listed here is left
# untagged rather than guessed at — the annotation pipeline's detector
# (see video-annotation-pipeline's meva_complete_classes) emits far more
# labels than the frontend has tags for (backpack, laptop, chair, dog...).
_OBJECT_TYPE_TO_TAG: dict[str, ClipTag] = {
    "person": "Person",
    "car": "Vehicle",
    "truck": "Vehicle",
    "bus": "Vehicle",
    "motorcycle": "Vehicle",
    "bicycle": "Vehicle",
}

# Keyword -> tag. Checked as a substring against the lowercased event_name.
# Order doesn't matter; all matches are kept (a name can trigger >1 tag,
# e.g. "Vehicle enters and departs" -> both Entry and Exit).
_EVENT_NAME_KEYWORDS: dict[str, ClipTag] = {
    "enter": "Entry",
    "arrive": "Entry",
    "pulls in": "Entry",
    "exit": "Exit",
    "leave": "Exit",
    "depart": "Exit",
    "loiter": "Loitering",
    "linger": "Loitering",
    "stand around": "Loitering",
    "abandon": "Object Left",
    "left unattended": "Object Left",
    "drops": "Object Left",
    "object left": "Object Left",
}


def derive_tags(object_types: list[str], event_name: str | None) -> list[ClipTag]:
    """object_types: raw detector labels for the objects involved in this
    event (e.g. from bronze.objects.label_details keys). event_name: the
    annotation pipeline's free-text event label (bronze.events.event_name),
    or None. Returns tags in a stable order, deduplicated."""
    tags: list[ClipTag] = []

    for object_type in object_types:
        tag = _OBJECT_TYPE_TO_TAG.get(object_type.strip().lower())
        if tag and tag not in tags:
            tags.append(tag)

    if event_name:
        lowered = event_name.lower()
        for keyword, tag in _EVENT_NAME_KEYWORDS.items():
            if keyword in lowered and tag not in tags:
                tags.append(tag)

    return tags
