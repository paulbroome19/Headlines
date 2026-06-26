"""add_story_id_to_normalisation_articles

Adds the missing `story_id` column to `data.normalisation_articles`.

This column is a plain denormalised bigint pointer — no FK to data.stories,
no index, nullable — matching the local Postgres definition exactly.

WHY: A third orphaned schema object from the same worktree session
(commit c3eecd5) that introduced data.stories and data.story_articles
(the local↔prod drift for those two was fixed in PR #20 / PR #21).
The cluster handler at cluster/handlers/requested.py:281 executes
    UPDATE data.normalisation_articles SET story_id = ...
which crashed production with `UndefinedColumn` because this column was
never captured in a migration.  A comprehensive cross-schema column diff
confirmed this is the only remaining local↔prod drift.

Revision ID: d5172e545f14
Revises: 977eade90b9f
Create Date: 2026-06-26 08:00:00.000000
"""
from alembic import op

revision = 'd5172e545f14'
down_revision = '977eade90b9f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE data.normalisation_articles ADD COLUMN story_id BIGINT
    """)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE data.normalisation_articles DROP COLUMN IF EXISTS story_id"
    )
