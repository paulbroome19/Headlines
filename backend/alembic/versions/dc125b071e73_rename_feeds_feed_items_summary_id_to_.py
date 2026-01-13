"""rename feeds.feed_items.summary_id to summarise_output_id

Revision ID: dc125b071e73
Revises: 0b74fcc0bd48
Create Date: 2026-01-11 18:48:24.905610

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dc125b071e73'
down_revision: Union[str, Sequence[str], None] = '0b74fcc0bd48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE feeds.feed_items RENAME COLUMN summary_id TO summarise_output_id")


def downgrade() -> None:
    op.execute("ALTER TABLE feeds.feed_items RENAME COLUMN summarise_output_id TO summary_id")
