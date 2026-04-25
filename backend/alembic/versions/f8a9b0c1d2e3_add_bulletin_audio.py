"""add_bulletin_audio

Creates data.bulletin_audio to cache TTS-generated audio per bulletin.
Cache key: UNIQUE(bulletin_id, script_hash, provider, voice, model, audio_format).

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-04-25 18:00:00.000000
"""
from alembic import op

revision = 'f8a9b0c1d2e3'
down_revision = 'e7f8a9b0c1d2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE data.bulletin_audio (
            id               SERIAL PRIMARY KEY,
            bulletin_id      INTEGER NOT NULL REFERENCES data.bulletins(id),
            script_hash      TEXT    NOT NULL,
            provider         TEXT    NOT NULL,
            voice            TEXT    NOT NULL,
            model            TEXT    NOT NULL,
            audio_format     TEXT    NOT NULL,
            storage_path     TEXT    NOT NULL,
            duration_seconds REAL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_bulletin_audio_cache
                UNIQUE (bulletin_id, script_hash, provider, voice, model, audio_format)
        )
    """)
    op.execute("""
        CREATE INDEX ix_bulletin_audio_bulletin_id
            ON data.bulletin_audio (bulletin_id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS data.ix_bulletin_audio_bulletin_id")
    op.execute("DROP TABLE IF EXISTS data.bulletin_audio")
