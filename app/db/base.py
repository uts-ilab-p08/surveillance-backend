from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    # This backend's tables live in their own "app" schema, separate from
    # the annotation team's "bronze" landing layer in the same shared
    # Supabase project. Alembic creates the schema itself (see the initial
    # migration) — Postgres doesn't create it automatically.
    metadata = MetaData(schema="app")
