"""introduce domain schemas and move event tables

Revision ID: a6b54d92b02b
Revises: fb09792b62dc
Create Date: 2026-01-11 09:52:57.014447

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a6b54d92b02b'
down_revision: Union[str, Sequence[str], None] = 'fb09792b62dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    This migration used to create schemas and move/rename event tables.

    After we moved creation of event.outbox and event.processed into the base
    migration (fb09792b62dc), this revision is intentionally a no-op to preserve
    the Alembic revision chain.
    """
    pass


def downgrade() -> None:
    """
    No-op downgrade.
    """
    pass