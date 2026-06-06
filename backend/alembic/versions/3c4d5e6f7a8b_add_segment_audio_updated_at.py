"""add updated_at to data.segment_audio

Revision ID: 3c4d5e6f7a8b
Revises: 2b3c4d5e6f7a
Create Date: 2026-04-29
"""
from alembic import op

revision = '3c4d5e6f7a8b'
down_revision = '2b3c4d5e6f7a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL = row has never been refreshed via upsert_stale().
    # Populated only on the stale-fallthrough write path.
    op.execute("""
        ALTER TABLE data.segment_audio
        ADD COLUMN updated_at TIMESTAMPTZ NULL
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE data.segment_audio
        DROP COLUMN IF EXISTS updated_at
    """)
