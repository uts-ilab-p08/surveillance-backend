"""Dedupe saved_queries and add a unique index on (user_id,
lower(trim(query_text))) — frontend spec §5.3. A user could otherwise
bookmark the same search text twice (the UI forgets its "already saved"
state after navigating away); this makes POST /queries/saved idempotent
per (user, normalized text) going forward, and cleans up any duplicates
that already exist.

Merge rule for existing duplicates: keep the oldest row (earliest
created_at) per (user_id, normalized text), carrying over the highest
`hits` value seen among the duplicates so a re-run count isn't lost.
"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = 'e362816aec7f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Carry the max hits among each duplicate group onto the row we're
    #    about to keep (the oldest one).
    op.execute("""
        WITH keepers AS (
            SELECT DISTINCT ON (user_id, lower(trim(query_text)))
                id, user_id, lower(trim(query_text)) AS norm_text
            FROM app.saved_queries
            ORDER BY user_id, lower(trim(query_text)), created_at ASC, id ASC
        ),
        max_hits AS (
            SELECT user_id, lower(trim(query_text)) AS norm_text, MAX(hits) AS hits
            FROM app.saved_queries
            GROUP BY user_id, lower(trim(query_text))
        )
        UPDATE app.saved_queries sq
        SET hits = m.hits
        FROM keepers k
        JOIN max_hits m ON m.user_id = k.user_id AND m.norm_text = k.norm_text
        WHERE sq.id = k.id
    """)

    # 2. Delete every row except the one kept per group.
    op.execute("""
        DELETE FROM app.saved_queries sq
        WHERE sq.id NOT IN (
            SELECT DISTINCT ON (user_id, lower(trim(query_text))) id
            FROM app.saved_queries
            ORDER BY user_id, lower(trim(query_text)), created_at ASC, id ASC
        )
    """)

    # 3. Enforce it going forward. A unique index on an expression, not a
    #    table CONSTRAINT, since Postgres constraints can't reference
    #    lower()/trim() directly — this still backs ON CONFLICT.
    op.execute("""
        CREATE UNIQUE INDEX uq_saved_queries_user_normalized_text
        ON app.saved_queries (user_id, lower(trim(query_text)))
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS app.uq_saved_queries_user_normalized_text")
