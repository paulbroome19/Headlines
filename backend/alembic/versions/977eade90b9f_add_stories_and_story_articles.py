"""add_stories_and_story_articles

Revision ID: 977eade90b9f
Revises: 4d5e6f7a8b9c
Create Date: 2026-06-25 12:00:00.000000

WHY THIS EXISTS
---------------
data.stories and data.story_articles were hand-created directly in local Postgres
during the cluster-handler worktree session (commit c3eecd5, "Push v1 Test"). They
were never captured in a migration. Because every local DB already had them, the gap
was invisible in development. The omission only surfaced as an UndefinedTable crash
in production on the first cluster-handler invocation. This migration backfills both
tables so a fresh clone and the production database match local exactly.
"""
from alembic import op

revision = '977eade90b9f'
down_revision = '4d5e6f7a8b9c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create data.stories first — story_articles has a FK back to it.
    op.execute("""
        CREATE TABLE data.stories (
            id                   BIGSERIAL    PRIMARY KEY,
            story_key            TEXT         NOT NULL,
            primary_entity_slug  TEXT,
            primary_category     TEXT,
            representative_title TEXT         NOT NULL,
            first_published_at   TIMESTAMPTZ,
            last_published_at    TIMESTAMPTZ,
            article_count        INTEGER      NOT NULL DEFAULT 1,
            created_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """)
    # Named index — an inline UNIQUE column constraint would produce a differently-named
    # constraint object and would not match local. A separate CREATE UNIQUE INDEX gives
    # exactly the name idx_stories_story_key that pg_dump shows.
    op.execute(
        "CREATE UNIQUE INDEX idx_stories_story_key ON data.stories (story_key)"
    )
    op.execute("""
        CREATE TABLE data.story_articles (
            story_id                 BIGINT      NOT NULL REFERENCES data.stories(id),
            normalisation_article_id BIGINT      NOT NULL REFERENCES data.normalisation_articles(id),
            is_primary               BOOLEAN     NOT NULL DEFAULT false,
            created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (story_id, normalisation_article_id)
        )
    """)


def downgrade() -> None:
    # Reverse FK order: drop story_articles before stories.
    op.execute("DROP TABLE IF EXISTS data.story_articles")
    op.execute("DROP TABLE IF EXISTS data.stories")
