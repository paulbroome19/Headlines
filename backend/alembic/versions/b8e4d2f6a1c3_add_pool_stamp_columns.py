"""add_pool_stamp_columns

Pool-aware ingestion (see docs/ingestion-pool-design.md Part 2). Each ingest run
= one (category, country) GNews pool; stamp that pool's category + country on the
run, and denormalise category + country + derived geo_region onto each article so
top-level topic and geographic routing are FREE (by construction, no inference)
and roll-up/region queries are a single indexed filter.

All columns are additive + nullable — existing rows and manual/legacy ingests
(no pool context) simply carry NULL.

Revision ID: b8e4d2f6a1c3
Revises: a7f3c1d9e2b4
Create Date: 2026-07-01 14:00:00.000000
"""
from alembic import op

revision = 'b8e4d2f6a1c3'
down_revision = 'a7f3c1d9e2b4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE data.ingestion_runs
            ADD COLUMN IF NOT EXISTS pool_category TEXT,
            ADD COLUMN IF NOT EXISTS pool_country  TEXT
    """)
    op.execute("""
        ALTER TABLE data.normalisation_articles
            ADD COLUMN IF NOT EXISTS pool_category TEXT,
            ADD COLUMN IF NOT EXISTS pool_country  TEXT,
            ADD COLUMN IF NOT EXISTS geo_region    TEXT
    """)
    # Roll-up / verification queries filter by region and coarse pool category.
    op.execute("CREATE INDEX IF NOT EXISTS idx_norm_articles_geo_region ON data.normalisation_articles (geo_region)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_norm_articles_pool_category ON data.normalisation_articles (pool_category)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS data.idx_norm_articles_pool_category")
    op.execute("DROP INDEX IF EXISTS data.idx_norm_articles_geo_region")
    op.execute("""
        ALTER TABLE data.normalisation_articles
            DROP COLUMN IF EXISTS geo_region,
            DROP COLUMN IF EXISTS pool_country,
            DROP COLUMN IF EXISTS pool_category
    """)
    op.execute("""
        ALTER TABLE data.ingestion_runs
            DROP COLUMN IF EXISTS pool_country,
            DROP COLUMN IF EXISTS pool_category
    """)
