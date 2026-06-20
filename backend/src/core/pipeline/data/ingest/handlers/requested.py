from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

from core.platform.config.settings import settings
from core.platform.db.session import SessionLocal
from core.platform.providers.gnews import GNewsClient
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo
from core.pipeline.data.ingest.repos.ingested_article_repo import IngestedArticleRepo
from core.pipeline.data.ingest.repos.ingestion_run_repo import IngestionRunRepo
from core.pipeline.data.normalise.events import DATA_NORMALISE_REQUESTED


def _dedup_hash_for_url(url: str) -> str:
    normalized = url.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _parse_published_at(published_at: Optional[str]) -> Optional[datetime]:
    if not published_at:
        return None
    try:
        # GNews: "2026-02-01T10:01:37Z"
        return datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except Exception:
        return None


def handle_ingest_requested(event: dict) -> None:
    """
    Event: data.ingest.requested

    Responsibilities (v1):
    - create data.ingestion_runs row
    - fetch articles from provider (GNews)
    - insert into data.ingested_articles (raw + metadata + dedup_hash)
    - enqueue data.normalise.requested into event.outbox
    """
    print(">>> INGEST HANDLER CALLED <<<", event)

    payload = event.get("payload") or {}

    # allow either "source" or "provider" naming
    source = payload.get("source") or payload.get("provider") or "gnews"
    trace_id = event.get("trace_id")

    query = payload.get("query")          # e.g. "Manchester United"
    topic = payload.get("topic")          # e.g. "sports"
    country = payload.get("country") or "gb"
    lang = payload.get("lang") or "en"
    max_results = int(payload.get("max_results") or 10)

    with SessionLocal() as db:
        runs = IngestionRunRepo(db)
        ingested = IngestedArticleRepo(db)
        outbox = OutboxRepo(db)

        # 1) Create run
        run = runs.create(source=source)

        # 2) Fetch from GNews (gated on settings.gnews_enabled)
        if not settings.gnews_enabled:
            print("ingest skipped: GNEWS_ENABLED=false")
            articles = []
        elif query:
            client = GNewsClient()
            articles = client.search(
                query=query,
                lang=lang,
                country=country,
                max_results=max_results,
            )
        else:
            client = GNewsClient()
            articles = client.top_headlines(
                lang=lang,
                country=country,
                max_results=max_results,
                topic=topic,
            )

        # 3) Insert into data.ingested_articles
        inserted_count = 0
        for a in articles:
            if not a.url:
                continue

            dedup_hash = _dedup_hash_for_url(a.url)

            # Count only new inserts (repo currently returns existing id for dedups)
            already = ingested.exists_by_dedup_hash(dedup_hash=dedup_hash)
            _ = ingested.create(
                ingestion_run_id=run.id,
                provider="gnews",
                provider_item_id=None,
                url=a.url,
                dedup_hash=dedup_hash,
                raw=a.raw,
                title=a.title,
                publisher=a.source_name,
                published_at=_parse_published_at(a.published_at),
            )
            if not already:
                inserted_count += 1

        # 4) Enqueue next pipeline step (normalise)
        outbox.add_event(
            Event(
                type=DATA_NORMALISE_REQUESTED,
                idempotency_key=f"normalise:{run.id}",
                payload={
                    "ingestion_run_id": run.id,
                    "source": source,
                    "inserted_count": inserted_count,
                },
                trace_id=trace_id,
            )
        )

        db.commit()