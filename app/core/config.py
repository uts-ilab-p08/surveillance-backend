"""
Centralized app configuration, loaded from environment variables / .env.

Every other module reads settings from here rather than calling os.environ
directly, so there is exactly one place that knows how config is sourced.
"""
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    api_v1_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:3000"

    # Database — this backend's own Postgres (users, video metadata, saved
    # queries). Does NOT hold embeddings/captions; that's the RAG repo's DB.
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/surveillance"

    # Auth (Basic Auth now; secret reserved for the future JWT upgrade)
    secret_key: str = "change-me-in-production"

    # Storage
    video_storage_backend: Literal["local", "supabase"] = "local"
    video_storage_path: str = "./data/videos"

    # --- Sibling services (separate repos/teammates) ---
    # Annotation pipeline: POST /annotate calls {annotation_service_url}/jobs.
    # Left unset in dev -> app/services/annotation_client.py logs and no-ops
    # instead of failing, so this repo runs standalone before that repo exists.
    annotation_service_url: str | None = None
    # Shared secret the annotation service must send back on its callback
    # (POST /videos/{id}/annotation-callback) so random callers can't flip
    # annotation_status. Simple header-based service auth, not user auth.
    annotation_callback_secret: str = "change-me-shared-secret"

    # RAG / vector search + LLM: GET /search calls {rag_service_url}/query.
    # Left unset in dev -> app/services/rag_client.py returns a canned mock
    # response so /search is exercisable before that repo exists.
    rag_service_url: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
