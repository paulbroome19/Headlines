"""
Cache for LLM-primary categorisations — mirrors the summarisation cache pattern
(story_summaries): a story is classified AT MOST ONCE per (content_hash, model), so a
re-cluster / event replay / backfill never pays for or re-rolls a decision.

The cache key is a content hash of everything the classifier sees (title + snippets +
pool hints + taxonomy version), so if the story's content or the taxonomy changes, it is
re-classified; otherwise the cached leaf is reused verbatim.

Schema is created lazily (CREATE TABLE IF NOT EXISTS) — the DDL also lives in
docs/sql/story_categorisations.sql for the deploy path. Additive; touches nothing else.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from .llm_categoriser import StoryCategorisation


def content_hash(
    *, title: str, snippets: list[str], pool_topic: str | None,
    pool_country: str | None, taxonomy_version: int,
) -> str:
    """16-char SHA-256 of the classifier's full input. Stable cache key."""
    payload = json.dumps(
        {
            "t": (title or "").strip(),
            "s": [(x or "").strip() for x in snippets],
            "pt": (pool_topic or "").strip().lower(),
            "pc": (pool_country or "").strip().lower(),
            "v": taxonomy_version,
        },
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


_DDL = """
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
"""

_ensured = False


class CategorisationRepo:
    def __init__(self, db: Session) -> None:
        self.db = db

    def ensure_table(self) -> None:
        """Create the cache table on first use (idempotent). Cheap after the first call."""
        global _ensured
        if _ensured:
            return
        self.db.execute(text(_DDL))
        self.db.commit()
        _ensured = True

    def get(self, content_hash: str, model: str) -> StoryCategorisation | None:
        row = self.db.execute(
            text("""
                SELECT primary_category, secondary_categories, confidence, reason, method
                FROM data.story_categorisations
                WHERE content_hash = :h AND model = :m
            """),
            {"h": content_hash, "m": model},
        ).first()
        if row is None:
            return None
        secs = row[1] if isinstance(row[1], list) else json.loads(row[1] or "[]")
        return StoryCategorisation(
            primary=row[0], secondaries=list(secs),
            confidence=row[2] or 0.0, reason=row[3] or "", method=row[4],
        )

    def put(
        self, *, story_id: int, content_hash: str, model: str,
        result: StoryCategorisation, taxonomy_version: int,
    ) -> None:
        self.db.execute(
            text("""
                INSERT INTO data.story_categorisations
                    (story_id, content_hash, model, primary_category,
                     secondary_categories, confidence, reason, method, taxonomy_version)
                VALUES (:sid, :h, :m, :p, CAST(:sec AS jsonb), :conf, :reason, :method, :v)
                ON CONFLICT (content_hash, model) DO UPDATE SET
                    primary_category = EXCLUDED.primary_category,
                    secondary_categories = EXCLUDED.secondary_categories,
                    confidence = EXCLUDED.confidence,
                    reason = EXCLUDED.reason,
                    method = EXCLUDED.method,
                    taxonomy_version = EXCLUDED.taxonomy_version
            """),
            {
                "sid": story_id, "h": content_hash, "m": model,
                "p": result.primary, "sec": json.dumps(result.secondaries),
                "conf": result.confidence, "reason": result.reason,
                "method": result.method, "v": taxonomy_version,
            },
        )
