"""add_story_summary_depth_tier

Depth-by-rank (docs/ranking-depth-design.md): the same story can now be summarised
at different depths (lead / major / standard / brief) depending on its rank in a
bulletin. So the summary cache key gains depth_tier — a story has ≤4 cache rows,
one per coarse tier, and presets share cached depths.

Existing summaries were the full ~120–180-word treatment → backfilled to 'lead'.

Revision ID: c9a1e7b3d5f2
Revises: a7f3c1d9e2b4
Create Date: 2026-07-01 16:00:00.000000
"""
from alembic import op

revision = 'c9a1e7b3d5f2'
down_revision = 'a7f3c1d9e2b4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing full-length summaries ≈ the 'lead' tier.
    op.execute("ALTER TABLE data.story_summaries ADD COLUMN IF NOT EXISTS depth_tier TEXT NOT NULL DEFAULT 'lead'")
    # Cache/uniqueness axis now includes depth: one row per (story, run, tier).
    op.execute("ALTER TABLE data.story_summaries DROP CONSTRAINT IF EXISTS uq_story_summaries_story_run")
    op.execute("""
        ALTER TABLE data.story_summaries
            ADD CONSTRAINT uq_story_summaries_story_run_depth
            UNIQUE (story_id, ranking_run_id, depth_tier)
    """)
    # Cross-run content cache lookup (story_id, content_hash, model, depth_tier).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_story_summaries_content_cache
        ON data.story_summaries (story_id, content_hash, model, depth_tier)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS data.idx_story_summaries_content_cache")
    op.execute("ALTER TABLE data.story_summaries DROP CONSTRAINT IF EXISTS uq_story_summaries_story_run_depth")
    op.execute("""
        ALTER TABLE data.story_summaries
            ADD CONSTRAINT uq_story_summaries_story_run UNIQUE (story_id, ranking_run_id)
    """)
    op.execute("ALTER TABLE data.story_summaries DROP COLUMN IF EXISTS depth_tier")
