"""Wire types for GET /videos/{video_id}/tracks — frontend spec §4.4."""
from pydantic import BaseModel


class TrackBox(BaseModel):
    t: float  # seconds into the video file, same time base as video_url
    x: float
    y: float
    w: float
    h: float
    confidence: float


class TrackObject(BaseModel):
    object_id: str
    label: str  # coarse, lowercase — "person", "vehicle", ...
    boxes: list[TrackBox]  # sorted by t, at most ~10 per second


class VideoTracksResponse(BaseModel):
    video_id: str
    frame_width: int
    frame_height: int
    objects: list[TrackObject]
