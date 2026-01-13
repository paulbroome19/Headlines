"""add feeds tables

Revision ID: d81441a036fd
Revises: 1ec2a96a3abd
Create Date: 2026-01-11 18:00:25.600580

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd81441a036fd'
down_revision: Union[str, Sequence[str], None] = '1ec2a96a3abd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feeds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(), nullable=False, server_default="system"),
        sa.Column("status", sa.String(), nullable=False, server_default="built"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema="feeds",
    )

    op.create_table(
        "feed_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("feed_id", sa.Integer(), nullable=False),
        sa.Column("summary_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.feeds.id"], ondelete="CASCADE"),
        schema="feeds",
    )

    op.create_index("ix_feed_items_feed_id", "feed_items", ["feed_id"], schema="feeds")
    op.create_index("ix_feed_items_summary_id", "feed_items", ["summary_id"], schema="feeds")


def downgrade() -> None:
    op.drop_index("ix_feed_items_summary_id", table_name="feed_items", schema="feeds")
    op.drop_index("ix_feed_items_feed_id", table_name="feed_items", schema="feeds")
    op.drop_table("feed_items", schema="feeds")
    op.drop_table("feeds", schema="feeds")
