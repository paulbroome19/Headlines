"""update_bulletins_for_filters

Adds request_hash, filters, story_count to data.bulletins.
Replaces UNIQUE(ranking_run_id) with UNIQUE(ranking_run_id, request_hash)
to support multiple cached bulletins per ranking run.

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-04-25 16:00:00.000000
"""
from alembic import op

revision = 'e7f8a9b0c1d2'
down_revision = 'd6e7f8a9b0c1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Clear existing test data — table was created this session, no production rows
    op.execute("DELETE FROM data.bulletins")

    # Drop the single-column unique constraint
    op.execute(
        "ALTER TABLE data.bulletins "
        "DROP CONSTRAINT bulletins_ranking_run_id_key"
    )

    # Add new columns
    op.execute("""
        ALTER TABLE data.bulletins
        ADD COLUMN request_hash TEXT    NOT NULL DEFAULT '',
        ADD COLUMN filters      JSONB   NOT NULL DEFAULT '{}',
        ADD COLUMN story_count  INTEGER NOT NULL DEFAULT 0
    """)

    # New composite unique constraint
    op.execute("""
        ALTER TABLE data.bulletins
        ADD CONSTRAINT uq_bulletins_run_request
        UNIQUE (ranking_run_id, request_hash)
    """)


def downgrade() -> None:
    op.execute("DELETE FROM data.bulletins")
    op.execute(
        "ALTER TABLE data.bulletins "
        "DROP CONSTRAINT uq_bulletins_run_request"
    )
    op.execute("""
        ALTER TABLE data.bulletins
        DROP COLUMN request_hash,
        DROP COLUMN filters,
        DROP COLUMN story_count
    """)
    op.execute("""
        ALTER TABLE data.bulletins
        ADD CONSTRAINT bulletins_ranking_run_id_key UNIQUE (ranking_run_id)
    """)
