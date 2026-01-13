"""add data.ingestion_runs

Revision ID: 148a5453628c
Revises: a6b54d92b02b
Create Date: 2026-01-11 12:44:35.188893

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '148a5453628c'
down_revision: Union[str, Sequence[str], None] = 'a6b54d92b02b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="started"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema="data",
    )


def downgrade() -> None:
    op.drop_table("ingestion_runs", schema="data")
