from pydantic import BaseModel


class VocabularyResponse(BaseModel):
    """GET /vocabulary — the values RAG can filter on, as stored in its
    Qdrant payloads. Every list is sorted; dates are ISO YYYY-MM-DD."""

    scenes: list[str]
    synonyms: dict[str, str]  # everyday word -> scene, only for scenes in `scenes`
    cameras: list[str]
    dates: list[str]
