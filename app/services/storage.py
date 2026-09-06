"""
Video file storage abstraction (diagram's "Video File Storage — Supabase
Storage (object store)"). VIDEO_STORAGE_BACKEND=local writes to disk so the
backend is runnable without a Supabase project during development;
=supabase is the production path once credentials exist.
"""
import shutil
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.config import get_settings


class StorageBackend:
    def save(self, file: UploadFile, video_id: uuid.UUID) -> str:
        raise NotImplementedError


class LocalStorageBackend(StorageBackend):
    def save(self, file: UploadFile, video_id: uuid.UUID) -> str:
        settings = get_settings()
        base = Path(settings.video_storage_path)
        base.mkdir(parents=True, exist_ok=True)
        suffix = Path(file.filename or "").suffix or ".mp4"
        dest = base / f"{video_id}{suffix}"
        with dest.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        return str(dest)


class SupabaseStorageBackend(StorageBackend):
    def save(self, file: UploadFile, video_id: uuid.UUID) -> str:
        raise NotImplementedError(
            "Wire up the Supabase Storage client here once a project/bucket exists."
        )


def get_storage_backend() -> StorageBackend:
    settings = get_settings()
    if settings.video_storage_backend == "supabase":
        return SupabaseStorageBackend()
    return LocalStorageBackend()
