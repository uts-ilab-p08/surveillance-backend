"""
Core metadata schema — this backend now only owns auth and (optionally)
saved searches. Video/camera metadata, captions, and embeddings all live
on the other two teams' side: raw video + annotations in the annotation
team's `bronze` schema and R2 bucket, vectors/search in the RAG service.
This backend is an auth layer plus a thin relay to that RAG service (see
app/services/rag_client.py) — it doesn't store or search video data itself.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    saved_queries: Mapped[list["SavedQuery"]] = relationship(back_populates="user")


class SavedQuery(Base):
    """A user's saved natural-language search, for history/re-run later."""

    __tablename__ = "saved_queries"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="saved_queries")
