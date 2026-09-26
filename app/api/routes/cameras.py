from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.clip import CameraDirectoryEntry, CamerasResponse
from app.services import bronze

router = APIRouter(tags=["cameras"])


@router.get("/cameras", response_model=CamerasResponse)
def get_cameras(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(get_current_user),
) -> dict:
    """§5 — the Cameras Directory modal, shared by Home and Results/Detail.
    Despite this doc's own opening scope note, this is in scope now: it's
    fully specified and already wired into two live screens."""
    rows = bronze.get_camera_directory(db)
    cameras = [
        CameraDirectoryEntry(
            code=row["code"],
            perspective=row["perspective"] or "",
            eventCount=row["event_count"],
            scene=row.get("scene"),
        )
        for row in rows
    ]
    return CamerasResponse(cameras=cameras)
