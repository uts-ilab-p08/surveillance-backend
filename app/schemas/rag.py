import uuid

from pydantic import BaseModel


class RagResultItem(BaseModel):
    video_id: uuid.UUID
    start_seconds: float
    end_seconds: float
    caption: str
    score: float


class RagQueryResult(BaseModel):
    """Shape returned by the RAG service — also what GET /search returns
    to the frontend, unmodified (the backend is a thin pass-through here)."""

    answer: str
    results: list[RagResultItem] = []
