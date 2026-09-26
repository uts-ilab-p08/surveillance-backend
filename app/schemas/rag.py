from pydantic import BaseModel

from app.schemas.clip import ClipTag


class RagResultItem(BaseModel):
    # NOTE: this is the annotation team's bronze.videos.video_id, which is a
    # SHA-256 hex digest of the original video name (see
    # video-annotation-pipeline/notebooks/06_export_meva_style_annotations.ipynb)
    # — a string, not a UUID. Don't assume it's one of this backend's own IDs;
    # this backend doesn't mint or store video IDs itself.
    video_id: str
    # Raw bronze.videos.video_url for now — NOT yet a signed, browser-
    # playable URL (frontend spec §5.1 "Video URLs must be playable").
    # Signing needs Supabase Storage createSignedUrl + service-role
    # access this backend doesn't have configured yet — flagged, not
    # silently faked.
    video_url: str | None = None
    start_seconds: float
    end_seconds: float
    caption: str
    score: float  # confidence, 0-1

    # --- §5.1 enrichment, from bronze via event_id -----------------------
    event_id: str | None = None  # opens /clips/{event_id}
    event_name: str | None = None  # card title (falls back to caption)
    description: str | None = None  # fuller text — same as caption today
    camera: str | None = None  # bronze.videos.camera_id
    scene: str | None = None  # bronze.videos.scene — confirmed the MEVA site name
    timestamp: str | None = None  # ISO 8601; see app/services/rag_client.py for the timezone caveat
    thumbnail_url: str | None = None  # not produced anywhere yet — needs an ffmpeg extraction step
    tags: list[ClipTag] = []  # via app/services/tagging.py, same as Clip.tags


class RagQueryResult(BaseModel):
    """Shape returned by the RAG service — also what GET /search returns
    to the frontend, unmodified (the backend is a thin pass-through here)."""

    answer: str
    results: list[RagResultItem] = []
