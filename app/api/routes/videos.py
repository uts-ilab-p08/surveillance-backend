from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.tracks import VideoTracksResponse
from app.services import bronze
from app.services.tracks import build_tracks

router = APIRouter(tags=["videos"])


@router.get("/videos/{video_id}/tracks", response_model=VideoTracksResponse)
def get_video_tracks(
    video_id: str,
    start_seconds: float = Query(..., ge=0),
    end_seconds: float = Query(..., ge=0),
    event_id: str | None = Query(default=None, description="Restrict to one event's objects"),
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(get_current_user),
) -> VideoTracksResponse:
    """§4.4 — real bounding boxes for the video player, replacing the
    frontend's SIMULATED box. Pure database read; no RAG call."""
    if end_seconds < start_seconds:
        raise HTTPException(status_code=422, detail="end_seconds must be >= start_seconds")

    dimensions = bronze.get_video_dimensions(db, video_id)
    if dimensions is None:
        raise HTTPException(status_code=404, detail="Unknown video_id")

    duration_seconds = dimensions.get("duration_seconds")
    if duration_seconds is not None and (start_seconds > duration_seconds or end_seconds > duration_seconds):
        raise HTTPException(
            status_code=422,
            detail=f"start_seconds/end_seconds must fall within the video's duration ({duration_seconds}s)",
        )

    rows = bronze.get_track_geometries(db, video_id, start_seconds, end_seconds, event_id)
    objects = build_tracks(rows)

    return VideoTracksResponse(
        video_id=video_id,
        frame_width=dimensions["frame_width"],
        frame_height=dimensions["frame_height"],
        objects=objects,
    )
