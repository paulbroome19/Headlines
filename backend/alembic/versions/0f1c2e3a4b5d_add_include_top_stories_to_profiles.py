"""add include_top_stories to profiles

Revision ID: 0f1c2e3a4b5d
Revises: a1b2c3d4e5f6
Create Date: 2026-04-27
"""
from alembic import op

revision = '0f1c2e3a4b5d'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE data.profiles
        ADD COLUMN include_top_stories BOOLEAN NOT NULL DEFAULT true
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE data.profiles DROP COLUMN include_top_stories")
