"""add data.normalisation_articles

Revision ID: a53b893ef645
Revises: 148a5453628c
Create Date: 2026-01-11 14:50:26.010980

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a53b893ef645'
down_revision: Union[str, Sequence[str], None] = '148a5453628c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None



def upgrade() -> None:
    op.create_table(
        "normalisation_articles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ingestion_run_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="normalised"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["data.ingestion_runs.id"],
            ondelete="CASCADE",
        ),
        schema="data",
    )
    op.create_index(
        "ix_normalisation_articles_ingestion_run_id",
        "normalisation_articles",
        ["ingestion_run_id"],
        schema="data",
    )


def downgrade() -> None:
    op.drop_index("ix_normalisation_articles_ingestion_run_id", table_name="normalisation_articles", schema="data")
    op.drop_table("normalisation_articles", schema="data")
