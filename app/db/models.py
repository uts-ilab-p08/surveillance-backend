"""
This backend's own Postgres schema ("app") — just per-user query history.
There is no local users table: identity is entirely Supabase Auth's (see
app/api/deps.py), so user_id here is a foreign key straight to Supabase's
own auth.users(id), a table this backend doesn't manage migrations for but
can safely reference since it already exists in the same Supabase project.

Video/camera metadata, captions, and embeddings all live on the other two
teams' side: raw video + annotations in the annotation team's `bronze`
schema and R2 bucket, vectors/search in the RAG service. This backend is
an auth-gated relay to that RAG service (see app/services/rag_client.py)
plus a read layer over `bronze` (see app/services/bronze.py) — it doesn't
store or search video data itself.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class SavedQuery(Base):
    """A user's explicitly-bookmarked search (Saved Queries screen). Created
    only via POST /queries/saved — never as a side effect of a plain search."""

    __tablename__ = "saved_queries"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.users.id"), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Count of times this exact query text has been re-run through
    # POST /search since being saved. Starts at 0 on save; POST /search
    # increments it when the incoming query text matches a saved one for
    # that user. Saving is a distinct action from running it (per the
    # frontend spec), so this must NOT be set on insert from search hits.
    hits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecentQuery(Base):
    """One row per search a user has actually run (Home screen's "Recent
    Queries" list). Written only as a side effect of POST /search — there
    is no independent create endpoint, per the frontend spec. Distinct from
    SavedQuery: every search lands here, only bookmarked ones go there."""

    __tablename__ = "recent_queries"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.users.id"), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Distinct camera count among that search's returned clips, per the
    # frontend's RecentQuery.cameras field. Computed once at search time
    # from the normalized Clip[] and stored, not recomputed on read.
    camera_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
