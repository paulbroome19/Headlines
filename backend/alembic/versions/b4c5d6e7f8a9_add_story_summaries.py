"""add_story_summaries

Revision ID: b4c5d6e7f8a9
Revises: 3e8f9a2b1c7d
Create Date: 2026-04-25 12:00:00.000000
"""
from alembic import op

revision = 'b4c5d6e7f8a9'
down_revision = '3e8f9a2b1c7d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE data.story_summaries (
            id                SERIAL PRIMARY KEY,
            story_id          TEXT        NOT NULL,
            ranking_run_id    INTEGER     NOT NULL REFERENCES data.ranking_runs(id),
            headline          TEXT        NOT NULL,
            summary_text      TEXT        NOT NULL,
            why_it_matters    TEXT,
            audio_script      TEXT,
            model             VARCHAR(100) NOT NULL,
            summary_version   INTEGER     NOT NULL DEFAULT 1,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_story_summaries_story_run UNIQUE (story_id, ranking_run_id)
        )
    """)
    op.execute(
        "CREATE INDEX ix_data_story_summaries_story_id "
        "ON data.story_summaries (story_id)"
    )
    op.execute(
        "CREATE INDEX ix_data_story_summaries_ranking_run_id "
        "ON data.story_summaries (ranking_run_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data.story_summaries")
