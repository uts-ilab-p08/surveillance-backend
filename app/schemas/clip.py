"""
Schemas for the shapes described in the frontend's Backend API Spec §2 —
Clip, RecentQuery, SavedQuery, CameraDirectoryEntry. These are the
frontend-facing output types; field names/casing here are load-bearing
(the frontend's TS types expect exact camelCase on thumbnailUrl/videoUrl/
savedOn/eventCount), so don't rename without checking that doc.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Closed enum per §2.1 — anything that doesn't map cleanly to one of these
# (see app/services/tagging.py) is omitted from Clip.tags, never forced.
ClipTag = Literal["Person", "Vehicle", "Entry", "Exit", "Loitering", "Object Left"]


class Clip(BaseModel):
    """The object every screen ultimately renders (§2.1). id is the
    bronze.events.event_id for this clip — stable and already unique, so
    there's no separate synthetic-id scheme to maintain (see backend
    architecture notes on this decision)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    camera: str
    code: str
    perspective: str
    ts: str  # "HH:MM:SS" — time of day the clip starts
    date: str  # e.g. "Aug 4"
    order: int  # chronological position within the response's clip list
    # Raw pass-through of RAG's own relevance score (0-1 float) — do not
    # scale by 100. If the frontend wants a percentage, it converts it
    # itself (team decision, 2026-09-21).
    confidence: float
    tags: list[ClipTag] = []
    objects: str  # free-text description of detected objects
    action: str  # free-text description of the detected action
    thumbnail_url: str | None = Field(default=None, alias="thumbnailUrl")
    video_url: str | None = Field(default=None, alias="videoUrl")


class RecentQueryOut(BaseModel):
    id: str
    text: str
    ts: str  # ISO timestamp string
    cameras: int


class SavedQueryOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    text: str
    saved_on: str = Field(alias="savedOn")  # ISO timestamp string
    hits: int


class SavedQueryCreate(BaseModel):
    text: str


class CameraDirectoryEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str
    perspective: str
    event_count: int = Field(alias="eventCount")
