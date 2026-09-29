"""
Clip thumbnails — GET /clips/{id}/thumbnail.jpg and the lazy
extract-then-cache service behind it (app/services/thumbnails.py).

Most tests patch _extract_frame to write fake JPEG bytes and mock bronze,
so they're about which frame gets picked, what gets cached, and how the
route maps failures. The _extract_frame tests at the bottom decode a real
MP4 generated on the fly with PyAV — no network, but the real seek/decode
path (the mocked tests alone missed that imageio-ffmpeg's static binary
segfaults on DNS lookups on Linux).
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import av
from PIL import Image

from app.api.deps import get_current_user
from app.db.session import get_db
from app.main import app
from app.services import bronze, thumbnails

EVENT_ID = "b44e6395ffcbaa0155c47966c951f1379dc347e5b452e872d67b2af0b531db08"
EVENT = {
    "event_id": EVENT_ID,
    "video_url": "https://example.com/video.mp4",
    "start_seconds": 100.0,
    "end_seconds": 110.0,
}
FAKE_JPEG = b"\xff\xd8\xff\xe0fake-jpeg"

client = TestClient(app)


@pytest.fixture(autouse=True)
def _cache_dir(tmp_path):
    with patch.object(thumbnails, "_cache_dir", return_value=tmp_path):
        yield tmp_path


@pytest.fixture(autouse=True)
def _db_override():
    def _get_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = _get_db
    yield
    app.dependency_overrides.pop(get_db, None)


def _fake_extract(video_url, seconds, dest):
    dest.write_bytes(FAKE_JPEG)


# --- service ---------------------------------------------------------------


def test_unknown_event_returns_none():
    with patch.object(bronze, "get_event_with_video", return_value=None):
        assert thumbnails.get_or_create(MagicMock(), EVENT_ID) is None


def test_malformed_event_id_returns_none_without_touching_the_db():
    with patch.object(bronze, "get_event_with_video") as get_event:
        assert thumbnails.get_or_create(MagicMock(), "../../etc/passwd") is None
    get_event.assert_not_called()


def test_event_without_video_url_returns_none():
    with patch.object(bronze, "get_event_with_video", return_value={**EVENT, "video_url": None}):
        assert thumbnails.get_or_create(MagicMock(), EVENT_ID) is None


def test_extracts_at_the_busiest_frame_and_caches_it(_cache_dir):
    with patch.object(bronze, "get_event_with_video", return_value=EVENT), \
         patch.object(bronze, "get_best_frame_seconds", return_value=104.5), \
         patch.object(thumbnails, "_extract_frame", side_effect=_fake_extract) as run:
        path = thumbnails.get_or_create(MagicMock(), EVENT_ID)

    assert path == _cache_dir / f"{EVENT_ID}.jpg"
    assert path.read_bytes() == FAKE_JPEG
    video_url, seconds, _ = run.call_args[0]
    assert video_url == EVENT["video_url"]
    assert seconds == 104.5


def test_falls_back_to_event_midpoint_when_no_detections():
    with patch.object(bronze, "get_event_with_video", return_value=EVENT), \
         patch.object(bronze, "get_best_frame_seconds", return_value=None), \
         patch.object(thumbnails, "_extract_frame", side_effect=_fake_extract) as run:
        thumbnails.get_or_create(MagicMock(), EVENT_ID)

    assert run.call_args[0][1] == 105.0


def test_cached_thumbnail_skips_db_and_extraction(_cache_dir):
    (_cache_dir / f"{EVENT_ID}.jpg").write_bytes(FAKE_JPEG)
    with patch.object(bronze, "get_event_with_video") as get_event, \
         patch.object(thumbnails, "_extract_frame") as run:
        path = thumbnails.get_or_create(MagicMock(), EVENT_ID)

    assert path.read_bytes() == FAKE_JPEG
    get_event.assert_not_called()
    run.assert_not_called()


def test_failed_extraction_raises_and_leaves_no_cached_file(_cache_dir):
    def _half_written_then_fail(video_url, seconds, dest):
        dest.write_bytes(b"partial")
        raise thumbnails.ThumbnailError("decode failed")

    with patch.object(bronze, "get_event_with_video", return_value=EVENT), \
         patch.object(bronze, "get_best_frame_seconds", return_value=104.5), \
         patch.object(thumbnails, "_extract_frame", side_effect=_half_written_then_fail):
        with pytest.raises(thumbnails.ThumbnailError):
            thumbnails.get_or_create(MagicMock(), EVENT_ID)

    assert list(_cache_dir.iterdir()) == []


def test_thumbnail_url_joins_base_prefix_and_event_id():
    url = thumbnails.thumbnail_url("https://api.example.com/", EVENT_ID)
    assert url == f"https://api.example.com/api/v1/clips/{EVENT_ID}/thumbnail.jpg"


# --- route -----------------------------------------------------------------


def test_route_serves_jpeg_with_long_cache_header(_cache_dir):
    (_cache_dir / f"{EVENT_ID}.jpg").write_bytes(FAKE_JPEG)
    resp = client.get(f"/api/v1/clips/{EVENT_ID}/thumbnail.jpg")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert "max-age" in resp.headers["cache-control"]
    assert resp.content == FAKE_JPEG


def test_route_needs_no_bearer_token(_cache_dir):
    """<img src> can't send an Authorization header, so this route must be
    public — the source MP4 already is (bronze.videos.video_url on R2)."""
    (_cache_dir / f"{EVENT_ID}.jpg").write_bytes(FAKE_JPEG)
    override = app.dependency_overrides.pop(get_current_user, None)
    try:
        resp = client.get(f"/api/v1/clips/{EVENT_ID}/thumbnail.jpg")
    finally:
        if override is not None:
            app.dependency_overrides[get_current_user] = override
    assert resp.status_code == 200


def test_route_returns_404_for_unknown_event():
    with patch.object(thumbnails, "get_or_create", return_value=None):
        resp = client.get(f"/api/v1/clips/{EVENT_ID}/thumbnail.jpg")
    assert resp.status_code == 404


def test_route_returns_502_when_extraction_fails():
    with patch.object(thumbnails, "get_or_create", side_effect=thumbnails.ThumbnailError("boom")):
        resp = client.get(f"/api/v1/clips/{EVENT_ID}/thumbnail.jpg")
    assert resp.status_code == 502


# --- _extract_frame, real decode ---------------------------------------------


def _make_video(path, fps=10, seconds=3, keyframe_every=10):
    """Solid-colour frames whose red channel encodes the frame index, so a
    decoded frame tells us which timestamp it came from."""
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        stream.codec_context.gop_size = keyframe_every
        for index in range(fps * seconds):
            image = Image.new("RGB", (64, 48), (index * 8, 0, 0))
            for packet in stream.encode(av.VideoFrame.from_image(image)):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def test_extract_frame_decodes_the_requested_moment_not_the_keyframe(tmp_path):
    video = tmp_path / "v.mp4"
    _make_video(video)
    dest = tmp_path / "out.jpg"

    # 1.5s at 10fps = frame 15, halfway between keyframes 10 and 20.
    thumbnails._extract_frame(str(video), 1.5, dest)

    with Image.open(dest) as image:
        assert image.format == "JPEG"
        red = image.getpixel((32, 24))[0]
    assert abs(red - 15 * 8) < 12


def test_extract_frame_past_the_end_raises(tmp_path):
    video = tmp_path / "v.mp4"
    _make_video(video)
    with pytest.raises(thumbnails.ThumbnailError):
        thumbnails._extract_frame(str(video), 99.0, tmp_path / "out.jpg")


def test_extract_frame_unreadable_source_raises(tmp_path):
    with pytest.raises(thumbnails.ThumbnailError):
        thumbnails._extract_frame(str(tmp_path / "missing.mp4"), 1.0, tmp_path / "out.jpg")
