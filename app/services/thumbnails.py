"""
Clip thumbnails: one JPEG frame per bronze event, extracted lazily from the
event's MP4 on first request and cached on local disk after that.

No frames exist anywhere upstream (bronze has geometries/bounding boxes
but no images, and the Supabase bucket is empty), so they're produced
here. The decoder seeks straight to the frame over HTTP range requests —
the R2 MP4s have their moov atom up front — so one extraction reads a
few MB around the target, not the whole video (~1-6s per frame,
network-bound, measured 2026-09-29).

Decoding is PyAV (in-process, dynamically linked ffmpeg libs from its
wheel) rather than an ffmpeg binary: Render's native Python runtime has
no system ffmpeg, and imageio-ffmpeg's static binary segfaults resolving
DNS on Linux (static glibc can't load NSS), so it can't open R2 URLs.
"""
import os
import re
import tempfile
import uuid
from pathlib import Path

import av
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services import bronze

# bronze event_ids are SHA-256 hex digests; anything else is rejected
# before it gets anywhere near a filesystem path.
_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_THUMBNAIL_WIDTH = 480
_JPEG_QUALITY = 80
# (open, read) socket timeouts for the HTTP source. Per network operation,
# not a wall-clock cap on the whole extraction.
_NETWORK_TIMEOUT_SECONDS = (10, 20)


class ThumbnailError(Exception):
    """The frame couldn't be produced (unreachable video, bad seek, timeout)."""


def _cache_dir() -> Path:
    configured = get_settings().thumbnail_cache_dir
    path = Path(configured) if configured else Path(tempfile.gettempdir()) / "clip-thumbnails"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _extract_frame(video_url: str, seconds: float, dest: Path) -> None:
    try:
        with av.open(video_url, timeout=_NETWORK_TIMEOUT_SECONDS) as container:
            stream = container.streams.video[0]
            # seek() lands on the keyframe at or before the target (~8s
            # apart in these videos); decode forward from there to the
            # exact moment instead of returning that keyframe.
            container.seek(int(seconds / stream.time_base), stream=stream)
            frame = next((f for f in container.decode(stream) if f.time is not None and f.time >= seconds), None)
            if frame is None:
                raise ThumbnailError(f"no frame at {seconds:.3f}s")
            image = frame.to_image()
    except av.FFmpegError as exc:
        raise ThumbnailError(str(exc)) from exc

    image.thumbnail((_THUMBNAIL_WIDTH, _THUMBNAIL_WIDTH))
    image.save(dest, format="JPEG", quality=_JPEG_QUALITY)


def _pick_seconds(db: Session, event: dict) -> float:
    best = bronze.get_best_frame_seconds(db, event["event_id"])
    if best is not None:
        return best
    start = event["start_seconds"] or 0.0
    end = event["end_seconds"] if event["end_seconds"] is not None else start
    return (start + end) / 2


def get_or_create(db: Session, event_id: str) -> Path | None:
    """Path to the cached JPEG for this event, extracting it first if
    needed. None when the event doesn't resolve to a playable video;
    raises ThumbnailError when it does but decoding fails."""
    if not _EVENT_ID_RE.match(event_id):
        return None

    path = _cache_dir() / f"{event_id}.jpg"
    if path.exists():
        return path

    event = bronze.get_event_with_video(db, event_id)
    if event is None or not event.get("video_url"):
        return None

    # Write to a temp name and rename into place, so a concurrent request
    # (or a crash mid-write) never serves a half-written JPEG.
    tmp = path.with_name(f".{event_id}.{uuid.uuid4().hex}.tmp.jpg")
    try:
        _extract_frame(event["video_url"], _pick_seconds(db, event), tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def thumbnail_url(base_url: str, event_id: str) -> str:
    return f"{base_url.rstrip('/')}{get_settings().api_v1_prefix}/clips/{event_id}/thumbnail.jpg"
