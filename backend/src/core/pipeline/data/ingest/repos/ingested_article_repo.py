from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class IngestedArticleRow:
    id: int
    ingestion_run_id: int
    provider: str
    dedup_hash: str


class IngestedArticleRepo:
    def __init__(self, db: Session):
        self.db = db

    def exists_by_dedup_hash(self, *, dedup_hash: str) -> bool:
        row = self.db.execute(
            text(
                """
                SELECT 1
                FROM data.ingested_articles
                WHERE dedup_hash = :dedup_hash
                LIMIT 1
                """
            ),
            {"dedup_hash": dedup_hash},
        ).first()
        return row is not None

    def create(
        self,
        *,
        ingestion_run_id: int,
        provider: str,
        provider_item_id: str | None,
        url: str | None,
        dedup_hash: str,
        raw: dict[str, Any],
        title: str | None,
        publisher: str | None,
        published_at: datetime | None,
    ) -> int:
        # Guard until DB-level unique constraint handles this
        if self.exists_by_dedup_hash(dedup_hash=dedup_hash):
            existing = self.db.execute(
                text(
                    """
                    SELECT id
                    FROM data.ingested_articles
                    WHERE dedup_hash = :dedup_hash
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ),
                {"dedup_hash": dedup_hash},
            ).scalar_one()
            return int(existing)

        raw_json = json.dumps(raw, ensure_ascii=False)

        new_id = self.db.execute(
            text(
                """
                INSERT INTO data.ingested_articles (
                    ingestion_run_id,
                    provider,
                    provider_item_id,
                    url,
                    dedup_hash,
                    raw,
                    title,
                    publisher,
                    published_at
                )
                VALUES (
                    :ingestion_run_id,
                    :provider,
                    :provider_item_id,
                    :url,
                    :dedup_hash,
                    CAST(:raw AS jsonb),
                    :title,
                    :publisher,
                    :published_at
                )
                RETURNING id
                """
            ),
            {
                "ingestion_run_id": int(ingestion_run_id),
                "provider": provider,
                "provider_item_id": provider_item_id,
                "url": url,
                "dedup_hash": dedup_hash,
                "raw": raw_json,
                "title": title,
                "publisher": publisher,
                "published_at": published_at,
            },
        ).scalar_one()

        return int(new_id)