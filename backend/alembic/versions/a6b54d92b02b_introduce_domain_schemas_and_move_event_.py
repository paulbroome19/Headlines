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
    """Upgrade schema."""

    # 1) Create domain schemas (idempotent)
    op.execute("CREATE SCHEMA IF NOT EXISTS event;")
    op.execute("CREATE SCHEMA IF NOT EXISTS data;")
    op.execute("CREATE SCHEMA IF NOT EXISTS bulletins;")
    op.execute("CREATE SCHEMA IF NOT EXISTS audio;")
    op.execute("CREATE SCHEMA IF NOT EXISTS users;")

    # 2) Move infra tables into event schema and rename
    # public.outbox_event -> event.outbox
    op.execute("ALTER TABLE IF EXISTS public.outbox_event SET SCHEMA event;")
    op.execute("ALTER TABLE IF EXISTS event.outbox_event RENAME TO outbox;")

    # public.processed_event -> event.processed
    op.execute("ALTER TABLE IF EXISTS public.processed_event SET SCHEMA event;")
    op.execute("ALTER TABLE IF EXISTS event.processed_event RENAME TO processed;")


def downgrade() -> None:
    """Downgrade schema."""

    # Reverse rename + move back to public
    op.execute("ALTER TABLE IF EXISTS event.outbox RENAME TO outbox_event;")
    op.execute("ALTER TABLE IF EXISTS event.outbox_event SET SCHEMA public;")

    op.execute("ALTER TABLE IF EXISTS event.processed RENAME TO processed_event;")
    op.execute("ALTER TABLE IF EXISTS event.processed_event SET SCHEMA public;")

    # We intentionally do NOT drop schemas in downgrade because later tables may exist.
