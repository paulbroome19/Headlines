"""add data.user_story_state

Revision ID: 4d5e6f7a8b9c
Revises: 3c4d5e6f7a8b
Create Date: 2026-04-30
"""
from alembic import op

revision = '4d5e6f7a8b9c'
down_revision = '3c4d5e6f7a8b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE data.user_story_state (
            id          SERIAL      PRIMARY KEY,
            profile_id  INTEGER     NOT NULL REFERENCES data.profiles(id),
            story_id    INTEGER     NOT NULL,
            story_hash  TEXT        NOT NULL,
            bulletin_id INTEGER     NOT NULL,
            state       TEXT        NOT NULL
                            CHECK (state IN ('queued', 'rejected', 'consumed')),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (profile_id, story_id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_user_story_state_profile_id
            ON data.user_story_state (profile_id)
    """)
    # Event handler queries by (profile_id, story_hash) — iOS reports hash, not story_id
    op.execute("""
        CREATE INDEX ix_user_story_state_profile_hash
            ON data.user_story_state (profile_id, story_hash)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data.user_story_state")
