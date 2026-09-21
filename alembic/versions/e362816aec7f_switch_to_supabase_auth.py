"""Switch identity to Supabase Auth — the backend no longer manages its own
users table or password hashing. The frontend authenticates users directly
against Supabase Auth (GoTrue), and this backend now verifies the JWT it
issues instead. saved_queries.user_id is repointed at Supabase's own
auth.users(id), and the now-unused local app.users table is dropped.

Note: auth.users already exists in the same Supabase project (Supabase
creates it), so this migration doesn't create it — only references it.
"""
from alembic import op
import sqlalchemy as sa


revision = 'e362816aec7f'
down_revision = 'f0031cacd045'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # saved_queries.user_id: was FK -> app.users.id, now -> auth.users.id.
    # Default Postgres-assigned name for the original single-column FK
    # created in d5121a7dd77a (no explicit name was given there).
    op.drop_constraint('saved_queries_user_id_fkey', 'saved_queries', schema='app', type_='foreignkey')
    op.create_foreign_key(
        'saved_queries_user_id_fkey',
        'saved_queries', 'users',
        ['user_id'], ['id'],
        source_schema='app',
        referent_schema='auth',
    )

    # The local users table (and its bcrypt password column) is no longer
    # used by the code — Supabase Auth owns identity now.
    op.drop_table('users', schema='app')


def downgrade() -> None:
    op.create_table('users',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('username', sa.String(), nullable=False),
        sa.Column('hashed_password', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        schema='app',
    )
    op.create_index(op.f('ix_app_users_username'), 'users', ['username'], unique=True, schema='app')

    op.drop_constraint('saved_queries_user_id_fkey', 'saved_queries', schema='app', type_='foreignkey')
    op.create_foreign_key(
        'saved_queries_user_id_fkey',
        'saved_queries', 'users',
        ['user_id'], ['id'],
        source_schema='app',
        referent_schema='app',
    )
