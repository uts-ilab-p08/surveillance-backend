"""
HTTP client for the Annotation Pipeline repo (diagram box 1 — a separate
service/repo owned by teammates). The backend does not run any
vision-language model itself; it only asks that service to process a video
and later receives a callback with the result.

Contract with that service (align on this with the pipeline team):
  Backend  -> POST {ANNOTATION_SERVICE_URL}/jobs
              {"video_id": "...", "video_url": "<signed/reachable storage URL>",
               "callback_url": "<this backend's /videos/{id}/annotation-callback>"}
  Service  -> POST {callback_url}
              {"status": "done" | "failed", "error": "..." (if failed)}
              header: X-Callback-Secret: <ANNOTATION_CALLBACK_SECRET>

If ANNOTATION_SERVICE_URL isn't set (e.g. local dev before that repo/service
exists), trigger_annotation() logs and returns without erroring, leaving the
video's status at "processing" — useful for exercising the rest of the API
before the sibling service is up.
"""
import logging

import httpx

from app.core.config import get_settings
from app.db.models import Video

logger = logging.getLogger(__name__)


class AnnotationServiceUnavailable(Exception):
    pass


def trigger_annotation(video: Video, callback_url: str) -> None:
    settings = get_settings()

    if not settings.annotation_service_url:
        logger.warning(
            "ANNOTATION_SERVICE_URL not configured; skipping real call for video %s. "
            "Set it once the annotation-pipeline service is deployed.",
            video.id,
        )
        return

    payload = {
        "video_id": str(video.id),
        "video_url": video.storage_path,
        "callback_url": callback_url,
    }
    try:
        resp = httpx.post(f"{settings.annotation_service_url}/jobs", json=payload, timeout=10.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise AnnotationServiceUnavailable(str(exc)) from exc
