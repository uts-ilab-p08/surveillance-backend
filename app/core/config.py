"""
Centralized app configuration, loaded from environment variables / .env.

Every other module reads settings from here rather than calling os.environ
directly, so there is exactly one place that knows how config is sourced.
"""
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    api_v1_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:3000"

    # Database — this backend's own Postgres schema ("app"), holding
    # per-user query history (saved_queries, recent_queries). No users
    # table of our own: identity is Supabase Auth's, see supabase_jwt_secret
    # below. Video/camera metadata, captions,
    # and embeddings all live outside this backend: raw video + annotations
    # in the annotation team's "bronze" schema and R2 bucket, vectors in the
    # RAG service's own store.
    #
    # Held as separate fields rather than one DATABASE_URL string: Supabase
    # passwords often contain characters (e.g. "@") that are only safe
    # inside a connection string once percent-encoded, and hand-building
    # that string is an easy way to silently connect to the wrong thing (or
    # not connect at all). URL.create() below does that encoding for us.
    db_user: str = "postgres"
    db_password: str = "postgres"
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "surveillance"

    @property
    def database_url(self) -> URL:
        """A SQLAlchemy URL object — pass directly to create_engine(), or
        call .render_as_string(hide_password=False) where a plain string is
        required (e.g. Alembic's config)."""
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    # Auth — identity lives entirely in Supabase Auth (GoTrue), not this
    # backend. The frontend logs users in directly against Supabase and
    # sends the resulting JWT as "Authorization: Bearer <token>"; this
    # backend only verifies that token's signature/expiry and reads the
    # user id out of its "sub" claim (see app/api/deps.py). There is no
    # /auth/register or /auth/login here and no local password storage —
    # Supabase's dashboard (Project Settings -> API -> JWT Secret) has this
    # value.
    supabase_jwt_secret: str = "change-me-in-production"

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
