import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User, Video
from app.db.session import get_db
from app.schemas.video import VideoOut
from app.services.storage import get_storage_backend

router = APIRouter(prefix="/videos", tags=["videos"])


@router.post("/upload", response_model=VideoOut, status_code=201)
def upload_video(
    file: UploadFile,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Video:
    """
    Not in the original diagram, but required by it: stage 1 starts from
    "Unannotated Video Clips from storage / new uploads" — something has to
    put clips into storage and create the metadata row before /annotate can
    run on them.
    """
    video = Video(storage_path="", original_filename=file.filename or "unknown")
    db.add(video)
    db.flush()  # assigns video.id without committing yet

    storage = get_storage_backend()
    video.storage_path = storage.save(file, video.id)
    db.commit()
    db.refresh(video)
    return video


@router.get("", response_model=list[VideoOut])
def list_videos(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[Video]:
    return db.query(Video).order_by(Video.uploaded_at.desc()).offset(skip).limit(min(limit, 200)).all()


@router.get("/{video_id}", response_model=VideoOut)
def get_video(
    video_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Video:
    """
    Returns this backend's own metadata + annotation_status only. Captions
    and clip-level search results live in the RAG service's data, reached
    via GET /search — not duplicated here (see README "Decisions").
    """
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return video
