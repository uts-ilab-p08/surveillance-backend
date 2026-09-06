import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class VideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_filename: str
    duration_seconds: float | None
    recorded_at: datetime | None
    uploaded_at: datetime
    annotation_status: str
    annotation_error: str | None
    camera_id: uuid.UUID | None


class AnnotateRequest(BaseModel):
    video_id: uuid.UUID


class AnnotateResponse(BaseModel):
    video_id: uuid.UUID
    status: str
    detail: str


class AnnotationCallback(BaseModel):
    """Body the annotation-pipeline service POSTs back to
    /videos/{video_id}/annotation-callback once a job finishes."""

    status: Literal["done", "failed"]
    error: str | None = None
