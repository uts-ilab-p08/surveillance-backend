from pydantic import BaseModel


class RagResultItem(BaseModel):
    # NOTE: this is the annotation team's bronze.videos.video_id, which is a
    # SHA-256 hex digest of the original video name (see
    # video-annotation-pipeline/notebooks/06_export_meva_style_annotations.ipynb)
    # — a string, not a UUID. Don't assume it's one of this backend's own IDs;
    # this backend doesn't mint or store video IDs itself.
    video_id: str
    video_url: str | None = None  # public R2 playback URL, when available
    start_seconds: float
    end_seconds: float
    caption: str
    score: float  # confidence, 0-1


class RagQueryResult(BaseModel):
    """Shape returned by the RAG service — also what GET /search returns
    to the frontend, unmodified (the backend is a thin pass-through here)."""

    answer: str
    results: list[RagResultItem] = []
