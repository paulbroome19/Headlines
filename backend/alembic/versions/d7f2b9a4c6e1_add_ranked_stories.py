"""add ranked_stories — the stable, insert-in-place ranked list

Moves ranking from continuous re-scoring (a new ranking_run re-scores the whole window
every ~15 min, so positions swing with recency) to a STABLE list. A story is scored ONCE
on time-invariant MERIT (coverage · prominence · category · authority — no freshness) when
it arrives and stays at that position. It is re-scored ONLY when its coverage genuinely
grows (source_count jumps), never on recency. It leaves the list only by ageing past 24h
(pruned by entered_list_at) or, per user, by being heard/skipped (applied in the request
path). Position = ORDER BY merit_score DESC, story_id.

Phase 1 (this migration): additive — the list is built alongside the existing ranking_runs
(dual-write). The request path is not changed yet.

Revision ID: d7f2b9a4c6e1
Revises: f3d5b7e9a2c1
Create Date: 2026-07-07 00:00:00.000000
"""
from alembic import op

revision = 'd7f2b9a4c6e1'
down_revision = 'f3d5b7e9a2c1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS data.ranked_stories (
            story_id         BIGINT      PRIMARY KEY,
            merit_score      REAL        NOT NULL,
            primary_category TEXT,
            top_story        BOOLEAN     NOT NULL DEFAULT false,
            source_count     INTEGER     NOT NULL DEFAULT 0,
            entered_list_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_scored_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Position is ORDER BY merit_score DESC, story_id — index the sort key.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ranked_stories_merit "
        "ON data.ranked_stories (merit_score DESC, story_id)"
    )
    # Pruning is by arrival age (the 24h rolling window).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ranked_stories_entered "
        "ON data.ranked_stories (entered_list_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data.ranked_stories")
