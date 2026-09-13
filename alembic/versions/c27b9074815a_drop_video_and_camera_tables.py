"""Drop videos/cameras — video metadata now lives entirely in the
annotation team's bronze schema + R2, not in this backend. The product
flow settled on search-only (query -> RAG -> top-K videos), with no
user-facing video upload for now, so this backend has no video data of
its own to store.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c27b9074815a'
down_revision = 'd5121a7dd77a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table('videos', schema='app')
    op.drop_table('cameras', schema='app')


def downgrade() -> None:
    op.create_table('cameras',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('location', sa.String(length=256), nullable=True),
    sa.Column('dataset', sa.String(length=64), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    schema='app'
    )
    op.create_table('videos',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('camera_id', sa.UUID(), nullable=True),
    sa.Column('storage_path', sa.String(length=512), nullable=False),
    sa.Column('original_filename', sa.String(length=256), nullable=False),
    sa.Column('duration_seconds', sa.Float(), nullable=True),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('annotation_status', sa.String(length=32), nullable=False),
    sa.Column('annotation_error', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['camera_id'], ['app.cameras.id'], ),
    sa.PrimaryKeyConstraint('id'),
    schema='app'
    )
