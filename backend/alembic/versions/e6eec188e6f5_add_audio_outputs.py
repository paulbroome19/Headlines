"""add audio outputs

Revision ID: e6eec188e6f5
Revises: cd73386e7f51
Create Date: 2026-01-18 13:40:15.883043

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6eec188e6f5'
down_revision: Union[str, Sequence[str], None] = 'cd73386e7f51'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

revision = "e6eec188e6f5"
down_revision = "cd73386e7f51"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outputs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("feed_id", sa.Integer(), nullable=False, index=True),
        sa.Column("scripts_output_id", sa.Integer(), nullable=False, unique=True, index=True),
        sa.Column("variant", sa.String(length=32), nullable=False, server_default="default"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="built"),
        sa.Column("format", sa.String(length=16), nullable=False, server_default="wav"),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema="audio",
    )

    op.create_index(
        "ix_audio_outputs_feed_id_created_at",
        "outputs",
        ["feed_id", "created_at"],
        unique=False,
        schema="audio",
    )


def downgrade() -> None:
    op.drop_index("ix_audio_outputs_feed_id_created_at", table_name="outputs", schema="audio")
    op.drop_table("outputs", schema="audio")
