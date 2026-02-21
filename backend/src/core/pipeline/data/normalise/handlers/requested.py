from sqlalchemy import text

from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo

from core.pipeline.data.summarise.events import DATA_SUMMARISE_REQUESTED
from core.pipeline.data.normalise.repos.normalisation_article_repo import (
    NormalisationArticleRepo,
)
from core.pipeline.data.normalise.categorise.entity_service import (
    categorise_article,
)


def handle_normalise_requested(event: dict) -> None:
    """
    Event: data.normalise.requested

    Responsibilities:
    - read data.ingested_articles for this ingestion_run_id
    - insert clean rows into data.normalisation_articles
    - categorise entities (deterministic keyword engine)
    - persist entity definitions + links
    - enqueue data.summarise.requested
    """

    payload = event.get("payload") or {}
    ingestion_run_id = payload.get("ingestion_run_id")
    trace_id = event.get("trace_id")

    if not ingestion_run_id:
        raise ValueError("ingestion_run_id missing in normalise event payload")

    with SessionLocal() as db:
        outbox = OutboxRepo(db)
        repo = NormalisationArticleRepo(db)

        rows = db.execute(
            text(
                """
                SELECT
                    ia.id,
                    ia.title,
                    ia.url,
                    ia.publisher,
                    ia.provider,
                    ia.published_at,
                    ia.raw
                FROM data.ingested_articles ia
                WHERE ia.ingestion_run_id = :ingestion_run_id
                ORDER BY ia.id ASC
                """
            ),
            {"ingestion_run_id": ingestion_run_id},
        ).mappings().all()

        inserted_count = 0

        for r in rows:
            if not r["title"] or not r["url"]:
                continue

            # 🔁 Idempotency guard
            exists = db.execute(
                text(
                    """
                    SELECT 1
                    FROM data.normalisation_articles
                    WHERE ingested_article_id = :ingested_article_id
                    LIMIT 1
                    """
                ),
                {"ingested_article_id": r["id"]},
            ).first()

            if exists:
                continue

            raw = r["raw"] or {}
            snippet = raw.get("description") or raw.get("content") or None

            if isinstance(snippet, str):
                snippet = snippet.strip()
                if not snippet:
                    snippet = None
                if snippet and len(snippet) > 800:
                    snippet = snippet[:800]

            # 1️⃣ Insert normalised article (with safe category defaults)
            normalised_id = db.execute(
                text(
                    """
                    INSERT INTO data.normalisation_articles (
                        ingestion_run_id,
                        ingested_article_id,
                        provider,
                        title,
                        url,
                        source,
                        published_at,
                        content_snippet,
                        status,
                        category_slugs,
                        category_primary,
                        category_method,
                        category_version
                    )
                    VALUES (
                        :ingestion_run_id,
                        :ingested_article_id,
                        :provider,
                        :title,
                        :url,
                        :source,
                        :published_at,
                        :content_snippet,
                        'normalised',
                        :category_slugs,
                        :category_primary,
                        :category_method,
                        :category_version
                    )
                    RETURNING id
                    """
                ),
                {
                    "ingestion_run_id": ingestion_run_id,
                    "ingested_article_id": r["id"],
                    "provider": r["provider"] or "unknown",
                    "title": r["title"],
                    "url": r["url"],
                    "source": r["publisher"] or "unknown",
                    "published_at": r["published_at"],
                    "content_snippet": snippet,
                    "category_slugs": [],
                    "category_primary": None,
                    "category_method": "unclassified",
                    "category_version": 1,
                },
            ).scalar_one()

            # 2️⃣ Entity categorisation
            result = categorise_article(
                title=r["title"],
                content_snippet=snippet,
            )

            for slug in result.entity_slugs:
                entity_id = repo.get_entity_id_by_slug(slug)

                if not entity_id:
                    meta = repo.get_registry_metadata(slug)
                    entity_id = repo.insert_entity(
                        slug=slug,
                        display_name=meta["display_name"],
                        entity_type=meta["entity_type"],
                    )

                repo.link_entity_to_article(
                    normalisation_article_id=normalised_id,
                    entity_id=entity_id,
                    is_primary=(slug == result.primary_entity),
                    confidence_score=result.scores.get(slug, 1.0),
                )

            inserted_count += 1

        next_event = Event(
            type=DATA_SUMMARISE_REQUESTED,
            idempotency_key=f"summarise:{ingestion_run_id}",
            payload={
                "ingestion_run_id": ingestion_run_id,
                "normalised_count": inserted_count,
            },
            trace_id=trace_id,
        )

        outbox.add_event(next_event)
        db.commit()