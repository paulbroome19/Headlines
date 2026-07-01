"""add user_daily_editions — stable per-user daily bulletin edition

A user's bulletin should be their EDITION OF THE DAY, not a fresh random draw each
tap. This table persists the ordered story-id set per (profile, edition_date,
request_hash); the manifest reuses it (dropping only heard stories, refilling freed
slots + splicing a genuinely bigger new lead) instead of re-rolling from scratch on
every new ranking run.

Revision ID: e2c4a6f8d1b9
Revises: d1f5a9c2e7b4
Create Date: 2026-07-01 21:20:00.000000
"""
from alembic import op

revision = 'e2c4a6f8d1b9'
down_revision = 'd1f5a9c2e7b4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS data.user_daily_editions (
            id            BIGSERIAL   PRIMARY KEY,
            profile_id    INTEGER     NOT NULL,
            edition_date  DATE        NOT NULL,
            request_hash  TEXT        NOT NULL,
            story_ids     JSONB       NOT NULL DEFAULT '[]'::jsonb,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_user_daily_edition UNIQUE (profile_id, edition_date, request_hash)
        )
        """
    )
    # Lookups are always by the unique key; the constraint's index covers them.


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data.user_daily_editions")
