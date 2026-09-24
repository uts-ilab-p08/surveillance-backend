from pydantic import BaseModel


class RagResultItem(BaseModel):
    # NOTE: this is the annotation team's bronze.videos.video_id, which is a
    # SHA-256 hex digest of the original video name (see
    # video-annotation-pipeline/notebooks/06_export_meva_style_annotations.ipynb)
    # — a string, not a UUID. Don't assume it's one of this backend's own IDs;
    # this backend doesn't mint or store video IDs itself.
    video_id: str
    video_url: str | None = None  # public R2 playback URL, when available
    # Nullable in bronze.events, and passed through as-is by the RAG package.
    start_seconds: float | None = None
    end_seconds: float | None = None
    caption: str  # event description, falling back to event_name
    score: float  # confidence, 0-1


class RagQueryResult(BaseModel):
    """What GET /search returns to the frontend — mapped from the RAG
    package's answer_query() response by app/services/rag_client.py."""

    answer: str
    results: list[RagResultItem] = []
