"""
GET /videos/{video_id}/tracks (spec §4.4) — route-level validation:
unknown video_id, end < start, and the new out-of-duration-bounds check.
build_tracks() itself is covered in tests/test_tracks.py; bronze is
mocked here so these are about this route's own branching.
"""
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.deps import CurrentUser, get_current_user
from app.main import app
from app.services import bronze

client = TestClient(app)
app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=uuid.uuid4(), email="test@example.com")


def test_unknown_video_id_returns_404():
    with patch.object(bronze, "get_video_dimensions", return_value=None):
        resp = client.get("/api/v1/videos/no-such-video/tracks", params={"start_seconds": 0, "end_seconds": 5})
    assert resp.status_code == 404


def test_end_before_start_returns_422():
    resp = client.get("/api/v1/videos/v1/tracks", params={"start_seconds": 10, "end_seconds": 5})
    assert resp.status_code == 422


def test_window_past_video_duration_returns_422():
    dims = {"frame_width": 1920, "frame_height": 1080, "duration_seconds": 300.0}
    with patch.object(bronze, "get_video_dimensions", return_value=dims):
        resp = client.get("/api/v1/videos/v1/tracks", params={"start_seconds": 290, "end_seconds": 310})
    assert resp.status_code == 422
    assert "duration" in resp.json()["detail"]


def test_window_within_duration_succeeds_with_no_detections():
    dims = {"frame_width": 1920, "frame_height": 1080, "duration_seconds": 300.0}
    with patch.object(bronze, "get_video_dimensions", return_value=dims), \
         patch.object(bronze, "get_track_geometries", return_value=[]):
        resp = client.get("/api/v1/videos/v1/tracks", params={"start_seconds": 28, "end_seconds": 35})
    assert resp.status_code == 200
    body = resp.json()
    assert body["objects"] == []
    assert body["frame_width"] == 1920


def test_missing_duration_skips_the_bounds_check_rather_than_erroring():
    dims = {"frame_width": 1920, "frame_height": 1080, "duration_seconds": None}
    with patch.object(bronze, "get_video_dimensions", return_value=dims), \
         patch.object(bronze, "get_track_geometries", return_value=[]):
        resp = client.get("/api/v1/videos/v1/tracks", params={"start_seconds": 9999, "end_seconds": 10000})
    assert resp.status_code == 200
