import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.models import User, Video
from app.db.session import get_db
from app.schemas.video import AnnotateRequest, AnnotateResponse, AnnotationCallback
from app.services.annotation_client import AnnotationServiceUnavailable, trigger_annotation

router = APIRouter(tags=["annotate"])


@router.post("/annotate", response_model=AnnotateResponse, status_code=202)
def annotate_video(
    body: AnnotateRequest,
    request: Request,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> AnnotateResponse:
    """
    Diagram's "POST /annotate — run pipeline on new videos". The backend
    does not run the vision-language model itself (that's the annotation-
    pipeline repo); it hands the job off over HTTP and immediately returns.
    The pipeline service reports completion asynchronously via
    POST /videos/{id}/annotation-callback. Poll GET /videos/{id} for status.
    """
    video = db.get(Video, body.video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    callback_url = str(request.url_for("annotation_callback", video_id=video.id))

    try:
        trigger_annotation(video, callback_url=callback_url)
    except AnnotationServiceUnavailable as exc:
        raise HTTPException(status_code=502, detail=f"Annotation service unreachable: {exc}") from exc

    video.annotation_status = "processing"
    video.annotation_error = None
    db.commit()

    return AnnotateResponse(video_id=video.id, status="processing", detail="Annotation job handed off")


@router.post("/videos/{video_id}/annotation-callback", name="annotation_callback", status_code=204)
def annotation_callback(
    video_id: uuid.UUID,
    body: AnnotationCallback,
    db: Session = Depends(get_db),
    x_callback_secret: str = Header(default=""),
) -> None:
    """
    Called by the annotation-pipeline service, not by end users — guarded by
    a shared secret header rather than the Basic Auth used for user-facing
    routes. Update ANNOTATION_CALLBACK_SECRET on both sides to match.
    """
    settings = get_settings()
    if x_callback_secret != settings.annotation_callback_secret:
        raise HTTPException(status_code=401, detail="Invalid callback secret")

    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    video.annotation_status = body.status
    video.annotation_error = body.error
    db.commit()
