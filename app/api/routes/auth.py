from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.db.models import User
from app.schemas.user import LoginResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(current_user: User = Depends(get_current_user)) -> LoginResponse:
    """
    Diagram's "POST /auth/login — issue session". With Basic Auth, the
    credentials themselves ARE the session on every request, so this
    endpoint's job is just to let the frontend verify a username/password
    pair once and cache "logged in" state client-side. It returns 200 iff
    get_current_user's dependency accepted the credentials.
    """
    return LoginResponse(username=current_user.username)
