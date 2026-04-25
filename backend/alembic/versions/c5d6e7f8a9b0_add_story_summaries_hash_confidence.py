"""add_story_summaries_hash_confidence

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-04-25 14:00:00.000000
"""
from alembic import op

revision = 'c5d6e7f8a9b0'
down_revision = 'b4c5d6e7f8a9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE data.story_summaries
        ADD COLUMN content_hash TEXT    NOT NULL DEFAULT '',
        ADD COLUMN confidence   REAL
    """)
    # Partial index: only rows that have a real hash (excludes legacy empty-string rows)
    op.execute("""
        CREATE INDEX ix_story_summaries_cache
        ON data.story_summaries (story_id, content_hash, model)
        WHERE content_hash != ''
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS data.ix_story_summaries_cache")
    op.execute("""
        ALTER TABLE data.story_summaries
        DROP COLUMN IF EXISTS content_hash,
        DROP COLUMN IF EXISTS confidence
    """)
