"""add scripts schema and outputs

Revision ID: cd73386e7f51
Revises: dc125b071e73
Create Date: 2026-01-12 00:24:00.184910

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cd73386e7f51'
down_revision: Union[str, Sequence[str], None] = 'dc125b071e73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # schema
    op.execute("CREATE SCHEMA IF NOT EXISTS scripts")

    # table
    op.create_table(
        "outputs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("feed_id", sa.Integer(), nullable=False, index=True),
        sa.Column("variant", sa.String(), nullable=False, server_default="default"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="built"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema="scripts",
    )


def downgrade() -> None:
    op.drop_table("outputs", schema="scripts")
    op.execute("DROP SCHEMA IF EXISTS scripts")
