"""add_bulletins

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-04-25 15:00:00.000000
"""
from alembic import op

revision = 'd6e7f8a9b0c1'
down_revision = 'c5d6e7f8a9b0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE data.bulletins (
            id              SERIAL      PRIMARY KEY,
            ranking_run_id  INTEGER     NOT NULL UNIQUE REFERENCES data.ranking_runs(id),
            script          TEXT        NOT NULL,
            segments        JSONB       NOT NULL DEFAULT '[]',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX ix_data_bulletins_ranking_run_id "
        "ON data.bulletins (ranking_run_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data.bulletins")
