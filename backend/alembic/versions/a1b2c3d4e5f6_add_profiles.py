"""add profiles

Revision ID: a1b2c3d4e5f6
Revises: f8a9b0c1d2e3
Create Date: 2026-04-25
"""
from alembic import op

revision = 'a1b2c3d4e5f6'
down_revision = 'f8a9b0c1d2e3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE data.profiles (
            id                 SERIAL PRIMARY KEY,
            name               TEXT NOT NULL,
            include_categories JSONB,
            exclude_categories JSONB,
            max_stories        INTEGER NOT NULL DEFAULT 8,
            voice              TEXT,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data.profiles")
