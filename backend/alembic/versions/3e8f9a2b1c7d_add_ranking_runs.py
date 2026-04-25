"""add data.ranking_runs

Revision ID: 3e8f9a2b1c7d
Revises: 4f02ef243332
Create Date: 2026-04-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3e8f9a2b1c7d"
down_revision: Union[str, Sequence[str], None] = "4f02ef243332"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ranking_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("batch_id", sa.Text(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "top_stories",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "briefing",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="data",
    )

    op.create_index(
        "ux_data_ranking_runs_batch_id",
        "ranking_runs",
        ["batch_id"],
        unique=True,
        schema="data",
    )

    op.create_index(
        "ix_data_ranking_runs_created_at",
        "ranking_runs",
        ["created_at"],
        schema="data",
        postgresql_ops={"created_at": "DESC"},
    )


def downgrade() -> None:
    op.drop_index("ix_data_ranking_runs_created_at", table_name="ranking_runs", schema="data")
    op.drop_index("ux_data_ranking_runs_batch_id", table_name="ranking_runs", schema="data")
    op.drop_table("ranking_runs", schema="data")
