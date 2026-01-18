"""create outbox and processed events

Revision ID: fb09792b62dc
Revises:
Create Date: 2026-01-10 23:21:37.932855
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "fb09792b62dc"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # For gen_random_uuid()
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    # Domain schemas
    op.execute("CREATE SCHEMA IF NOT EXISTS event;")
    op.execute("CREATE SCHEMA IF NOT EXISTS data;")
    op.execute("CREATE SCHEMA IF NOT EXISTS feeds;")
    op.execute("CREATE SCHEMA IF NOT EXISTS scripts;")
    op.execute("CREATE SCHEMA IF NOT EXISTS audio;")
    op.execute("CREATE SCHEMA IF NOT EXISTS users;")

    # event.outbox
    op.create_table(
        "outbox",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        schema="event",
    )

    op.create_index(
        "ix_event_outbox_idempotency_key",
        "outbox",
        ["idempotency_key"],
        unique=False,
        schema="event",
    )
    op.create_index(
        "ix_event_outbox_status_created_at",
        "outbox",
        ["status", "created_at"],
        unique=False,
        schema="event",
    )

    # event.processed
    op.create_table(
        "processed",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="event",
    )

    # Option A (keep as you have): globally unique idempotency keys
    op.create_index(
        "ix_event_processed_idempotency_key",
        "processed",
        ["idempotency_key"],
        unique=True,
        schema="event",
    )

    # Option B (safer long-term): uncomment for composite uniqueness
    # op.create_index(
    #     "ux_event_processed_type_idem",
    #     "processed",
    #     ["event_type", "idempotency_key"],
    #     unique=True,
    #     schema="event",
    # )


def downgrade() -> None:
    op.drop_index("ix_event_processed_idempotency_key", table_name="processed", schema="event")
    op.drop_table("processed", schema="event")

    op.drop_index("ix_event_outbox_status_created_at", table_name="outbox", schema="event")
    op.drop_index("ix_event_outbox_idempotency_key", table_name="outbox", schema="event")
    op.drop_table("outbox", schema="event")