from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import CurrentUser, get_current_user
from app.schemas.vocabulary import VocabularyResponse
from app.services import rag_client
from app.services.rag_client import RagServiceUnavailable

router = APIRouter(tags=["vocabulary"])


@router.get("/vocabulary", response_model=VocabularyResponse)
def get_vocabulary(_user: CurrentUser = Depends(get_current_user)) -> dict:
    """The locations, cameras and dates the query field can name, for the
    search field's Location/Date/Camera menus. No get_db: this never
    touches Postgres, only the RAG's index."""
    try:
        return rag_client.vocabulary()
    except RagServiceUnavailable as exc:
        raise HTTPException(status_code=502, detail=f"Search service unreachable: {exc}") from exc
