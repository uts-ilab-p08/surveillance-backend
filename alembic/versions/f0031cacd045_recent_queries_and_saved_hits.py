"""Add recent_queries table and saved_queries.hits — Saved Queries is back
in scope (per team decision), and Recent Queries needs its own table since
every search populates it, not just bookmarked ones.
"""
from alembic import op
import sqlalchemy as sa


revision = 'f0031cacd045'
down_revision = 'c27b9074815a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('saved_queries', sa.Column('hits', sa.Integer(), nullable=False, server_default='0'), schema='app')

    op.create_table('recent_queries',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('query_text', sa.Text(), nullable=False),
    sa.Column('camera_count', sa.Integer(), nullable=False, server_default='0'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['auth.users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    schema='app'
    )


def downgrade() -> None:
    op.drop_table('recent_queries', schema='app')
    op.drop_column('saved_queries', 'hits', schema='app')
