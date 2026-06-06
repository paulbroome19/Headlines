"""add data.segment_audio

Revision ID: 2b3c4d5e6f7a
Revises: 1a2b3c4d5e6f
Create Date: 2026-04-29
"""
from alembic import op

revision = '2b3c4d5e6f7a'
down_revision = '1a2b3c4d5e6f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE data.segment_audio (
            id               SERIAL PRIMARY KEY,
            script_hash      TEXT NOT NULL,
            voice            TEXT NOT NULL,
            model            TEXT NOT NULL,
            audio_format     TEXT NOT NULL,
            segment_type     TEXT NOT NULL,
            character_count  INTEGER,
            storage_path     TEXT NOT NULL,
            audio_url        TEXT,
            duration_seconds REAL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (script_hash, voice, model, audio_format)
        )
    """)
    # Analytics index — segment_type is never part of cache lookups
    op.execute("""
        CREATE INDEX ix_segment_audio_type ON data.segment_audio (segment_type)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data.segment_audio")
