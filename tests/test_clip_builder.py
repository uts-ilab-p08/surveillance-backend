"""
app/services/clip_builder.py: bronze event+video row -> frontend Clip.
bronze is stubbed out — this is about the mapping, not the database.
"""
from datetime import datetime
from unittest.mock import patch

from app.services import bronze
from app.services.clip_builder import build_clip


def _bronze_event(**overrides) -> dict:
    """A row from bronze.get_event_with_video."""
    event = {
        "event_id": "ev1",
        "event_name": "Person exits a white SUV",
        "description": "A person in a dark jacket is seen exiting a white SUV.",
        "start_seconds": 60.0,
        "end_seconds": 70.0,
        "video_id": "vid1",
        "camera_id": "G340",
        "scene": "bus",
        "capture_start_local": datetime(2018, 3, 5, 13, 15, 0),
        "capture_time_zone": "unknown",
        "video_url": "https://example.com/clip.mp4",
    }
    event.update(overrides)
    return event


def _build(event: dict):
    with patch.object(bronze, "get_object_types_for_event", return_value=[]):
        return build_clip(object(), event, order=0, confidence=0.5)


def test_clip_exposes_capture_start_local_without_offset():
    clip = _build(_bronze_event())

    assert clip.capture_start_local == "2018-03-05T13:15:00"
    assert clip.model_dump(by_alias=True)["captureStartLocal"] == "2018-03-05T13:15:00"


def test_clip_capture_start_local_is_none_when_bronze_has_none():
    clip = _build(_bronze_event(capture_start_local=None))

    assert clip.capture_start_local is None
