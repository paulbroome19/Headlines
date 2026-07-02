-- Cache for LLM-primary categorisations (docs/categorisation-llm-primary.md).
-- The app also creates this lazily (CREATE TABLE IF NOT EXISTS) on first use; this file
-- is the explicit DDL for the deploy/migration path. Additive — touches nothing else.

CREATE TABLE IF NOT EXISTS data.story_categorisations (
    id                   BIGSERIAL PRIMARY KEY,
    story_id             BIGINT,
    content_hash         TEXT NOT NULL,
    model                TEXT NOT NULL,
    primary_category     TEXT NOT NULL,
    secondary_categories JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence           REAL,
    reason               TEXT,
    method               TEXT NOT NULL,
    taxonomy_version     INTEGER NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (content_hash, model)
);
