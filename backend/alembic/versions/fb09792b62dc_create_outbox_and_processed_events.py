"""create outbox and processed events

Revision ID: fb09792b62dc
Revises: 
Create Date: 2026-01-10 23:21:37.932855

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'fb09792b62dc'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) Create domain schemas (idempotent)
    op.execute("CREATE SCHEMA IF NOT EXISTS event;")
    op.execute("CREATE SCHEMA IF NOT EXISTS data;")
    op.execute("CREATE SCHEMA IF NOT EXISTS bulletins;")
    op.execute("CREATE SCHEMA IF NOT EXISTS audio;")
    op.execute("CREATE SCHEMA IF NOT EXISTS users;")

    # 2) Move infra tables into event schema and rename
    # outbox_event -> event.outbox
    op.execute("ALTER TABLE IF EXISTS public.outbox_event SET SCHEMA event;")
    op.execute("ALTER TABLE IF EXISTS event.outbox_event RENAME TO outbox;")

    # processed_event -> event.processed
    op.execute("ALTER TABLE IF EXISTS public.processed_event SET SCHEMA event;")
    op.execute("ALTER TABLE IF EXISTS event.processed_event RENAME TO processed;")


def downgrade() -> None:
    # Reverse rename + move back to public
    op.execute("ALTER TABLE IF EXISTS event.outbox RENAME TO outbox_event;")
    op.execute("ALTER TABLE IF EXISTS event.outbox_event SET SCHEMA public;")

    op.execute("ALTER TABLE IF EXISTS event.processed RENAME TO processed_event;")
    op.execute("ALTER TABLE IF EXISTS event.processed_event SET SCHEMA public;")


def downgrade() -> None:
    op.drop_table("processed_event")
    op.drop_index("ix_outbox_event_idempotency_key", table_name="outbox_event")
    op.drop_index("ix_outbox_event_status_created_at", table_name="outbox_event")
    op.drop_table("outbox_event")
