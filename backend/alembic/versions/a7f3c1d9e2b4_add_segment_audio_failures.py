"""add_segment_audio_failures

Adds `data.segment_audio_failures` — records a TTS segment that genuinely failed
to generate after retries, so the API can report an HONEST, distinguishable
"failed" state (vs "still generating") to iOS for graceful loading/buffering.

Additive and self-contained: does NOT touch the hot `data.segment_audio` cache
table, so the working generation/caching path is unaffected. A row here means
"this exact segment text (for this voice/model/format) failed N attempts"; it is
cleared automatically when the segment later synthesises successfully.

Revision ID: a7f3c1d9e2b4
Revises: d5172e545f14
Create Date: 2026-06-30 11:00:00.000000
"""
from alembic import op

revision = 'a7f3c1d9e2b4'
down_revision = 'd5172e545f14'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS data.segment_audio_failures (
            script_hash   TEXT        NOT NULL,
            voice         TEXT        NOT NULL,
            model         TEXT        NOT NULL,
            audio_format  TEXT        NOT NULL,
            attempts      INTEGER     NOT NULL DEFAULT 1,
            last_error    TEXT,
            failed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (script_hash, voice, model, audio_format)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data.segment_audio_failures")
