"""
Core metadata schema — this is ALL the backend repo owns per the diagram's
PostgreSQL box: "video metadata, users + credentials, saved queries."

Captions, embeddings, and the vector index live in the RAG team's repo/
database, not here — the backend never stores or searches embeddings
itself, it only calls that service (see app/services/rag_client.py). That
keeps this schema decoupled from whatever embedding model/dimension they
end up choosing.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
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


class Camera(Base):
    """A physical/logical camera feed within a multi-camera dataset."""

    __tablename__ = "cameras"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    dataset: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. "MEVA", "VIRAT"

    videos: Mapped[list["Video"]] = relationship(back_populates="camera")


class Video(Base):
    """A single stored video file/clip referenced from Video File Storage."""

    __tablename__ = "videos"

    id: Mapped[uuid.UUID] = _uuid_pk()
    camera_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cameras.id"), nullable=True)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)  # bucket key / local path
    original_filename: Mapped[str] = mapped_column(String(256), nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(nullable=True)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # pending -> processing (annotation service accepted the job) -> done | failed
    # (set by the annotation service calling back into
    # POST /videos/{id}/annotation-callback, see app/api/routes/annotate.py)
    annotation_status: Mapped[str] = mapped_column(String(32), default="pending")
    annotation_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    camera: Mapped["Camera | None"] = relationship(back_populates="videos")


class SavedQuery(Base):
    """Plus/future: a user's saved natural-language search."""

    __tablename__ = "saved_queries"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="saved_queries")
