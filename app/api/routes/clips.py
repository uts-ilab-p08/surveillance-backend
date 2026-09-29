from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.clip import Clip, ClipListResponse
from app.services import bronze, thumbnails
from app.services.clip_builder import build_clip

router = APIRouter(tags=["clips"])


@router.get("/clips/{id}", response_model=Clip)
def get_clip(
    id: str,
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(get_current_user),
) -> Clip:
    """Fetches a single clip by id (§4, GET /clips/{id}). id is the
    bronze.events.event_id — see the Clip.id design note in
    app/schemas/clip.py. 404 when it doesn't resolve, e.g. a stale link."""
    event = bronze.get_event_with_video(db, id)
    if event is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    return build_clip(db, event, order=0)


@router.get("/clips/{id}/related", response_model=ClipListResponse)
def get_related_clips(
    id: str,
    limit: int = Query(default=4, ge=1, le=20),
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Powers the "Related Clips" panel (§4). Relatedness heuristic is ours
    to define (not specified by the frontend contract): same camera,
    closest in time — see app/services/bronze.get_related_events."""
    anchor = bronze.get_event_with_video(db, id)
    if anchor is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    related = bronze.get_related_events(db, anchor, limit=limit)
    clips = [build_clip(db, event, order=index) for index, event in enumerate(related)]
    return ClipListResponse(clips=clips)


@router.get("/clips/{id}/thumbnail.jpg", response_class=FileResponse)
def get_clip_thumbnail(id: str, db: Session = Depends(get_db)) -> FileResponse:
    """One representative JPEG frame for the clip, extracted on first
    request and cached after that (see app/services/thumbnails.py).

    Deliberately unauthenticated: it's loaded via <img src>, which can't
    send a Bearer token, and the source MP4 is already public on R2."""
    try:
        path = thumbnails.get_or_create(db, id)
    except thumbnails.ThumbnailError as exc:
        raise HTTPException(status_code=502, detail=f"Could not extract thumbnail: {exc}") from exc
    if path is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    # A clip's frame never changes, so let browsers/CDNs keep it.
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800, immutable"})
