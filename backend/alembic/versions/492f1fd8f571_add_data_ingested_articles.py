"""add data.ingested_articles

Revision ID: 492f1fd8f571
Revises: e6eec188e6f5
Create Date: 2026-02-08 20:28:37.244937
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "492f1fd8f571"
down_revision: Union[str, Sequence[str], None] = "e6eec188e6f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ensure schema exists (safe/idempotent)
    op.execute("CREATE SCHEMA IF NOT EXISTS data;")

    # Optional but useful elsewhere; safe if already enabled
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    op.create_table(
        "ingested_articles",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),

        # Which ingestion run produced this row
        sa.Column("ingestion_run_id", sa.Integer(), nullable=False),

        # Provider identity (e.g. "gnews")
        sa.Column("provider", sa.String(length=32), nullable=False),

        # Provider-specific identifier if available
        sa.Column("provider_item_id", sa.String(length=512), nullable=True),

        # Canonical URL (best-effort)
        sa.Column("url", sa.Text(), nullable=True),

        # Stable hash used for dedup across providers / runs
        sa.Column("dedup_hash", sa.String(length=64), nullable=False),

        # Raw provider payload for reprocessing/debugging/audit
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),

        # Useful searchable fields (optional but helpful)
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("publisher", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),

        # When we ingested it
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),

        schema="data",
    )

    # FK to data.ingestion_runs
    op.create_foreign_key(
        "fk_ingested_articles_ingestion_run_id",
        source_table="ingested_articles",
        referent_table="ingestion_runs",
        local_cols=["ingestion_run_id"],
        remote_cols=["id"],
        source_schema="data",
        referent_schema="data",
        ondelete="CASCADE",
    )

    # Core dedup primitive (protects against concurrency races)
    op.create_index(
        "ux_data_ingested_articles_dedup_hash",
        "ingested_articles",
        ["dedup_hash"],
        unique=True,
        schema="data",
    )

    # Fast lookup per run (2_normalise step)
    op.create_index(
        "ix_data_ingested_articles_ingestion_run_id",
        "ingested_articles",
        ["ingestion_run_id"],
        unique=False,
        schema="data",
    )

    # Optional: provider/time querying
    op.create_index(
        "ix_data_ingested_articles_provider_created_at",
        "ingested_articles",
        ["provider", "created_at"],
        unique=False,
        schema="data",
    )

    # Optional: publish-time querying
    op.create_index(
        "ix_data_ingested_articles_published_at",
        "ingested_articles",
        ["published_at"],
        unique=False,
        schema="data",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_data_ingested_articles_published_at",
        table_name="ingested_articles",
        schema="data",
    )
    op.drop_index(
        "ix_data_ingested_articles_provider_created_at",
        table_name="ingested_articles",
        schema="data",
    )
    op.drop_index(
        "ix_data_ingested_articles_ingestion_run_id",
        table_name="ingested_articles",
        schema="data",
    )
    op.drop_index(
        "ux_data_ingested_articles_dedup_hash",
        table_name="ingested_articles",
        schema="data",
    )

    op.drop_constraint(
        "fk_ingested_articles_ingestion_run_id",
        "ingested_articles",
        schema="data",
        type_="foreignkey",
    )

    op.drop_table("ingested_articles", schema="data")