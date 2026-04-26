"""add_audio_url_to_bulletin_audio

Adds audio_url TEXT column to data.bulletin_audio.
Null for local storage; populated with public/CDN URL in S3 mode.

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-04-26 20:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'b3c4d5e6f7a8'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'bulletin_audio',
        sa.Column('audio_url', sa.Text(), nullable=True),
        schema='data',
    )


def downgrade() -> None:
    op.drop_column('bulletin_audio', 'audio_url', schema='data')
