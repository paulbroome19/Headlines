"""remove dead surface: merge the 4 alembic heads, fold ranking_run_id, drop dead chain tables

Audit §6/§7 (alembic hygiene, item 8). Three things:
  1. Merge the 4 divergent alembic heads (c9a1e7b3d5f2, d7f2b9a4c6e1, b8e4d2f6a1c3,
     e6eec188e6f5) into one linear head — the multi-head graph is what forced runtime
     ADD COLUMN IF NOT EXISTS workarounds twice.
  2. Fold the runtime-ensured column into proper DDL: data.user_daily_editions.ranking_run_id
     (was selection._ensure_columns). (editorial_reviews.top_story stays in its own runtime
     CREATE TABLE, which already declares the column; its redundant ALTER was removed.)
  3. Drop the dead feeds→scripts→audio chain tables — verified 0 rows in prod (§6 item 6).

Revision ID: f0dead5e1a2b
Revises: c9a1e7b3d5f2, d7f2b9a4c6e1, b8e4d2f6a1c3, e6eec188e6f5
Create Date: 2026-07-08
"""
from alembic import op

revision = 'f0dead5e1a2b'
down_revision = ('c9a1e7b3d5f2', 'd7f2b9a4c6e1', 'b8e4d2f6a1c3', 'e6eec188e6f5')
branch_labels = None
depends_on = None


def upgrade() -> None:
    # (2) Fold the runtime-ensured column (was selection._ensure_columns).
    op.execute("ALTER TABLE data.user_daily_editions ADD COLUMN IF NOT EXISTS ranking_run_id BIGINT")

    # (3) Drop the dead feeds→scripts→audio chain (0 rows in prod).
    op.execute("DROP TABLE IF EXISTS data.summarise_outputs")
    op.execute("DROP TABLE IF EXISTS data.audio_outputs")
    op.execute("DROP SCHEMA IF EXISTS feeds CASCADE")
    op.execute("DROP SCHEMA IF EXISTS scripts CASCADE")


def downgrade() -> None:
    # One-way cleanup — the dead chain is gone from the codebase, so there is nothing to
    # recreate; only the folded column is reversible.
    op.execute("ALTER TABLE data.user_daily_editions DROP COLUMN IF EXISTS ranking_run_id")
