from sqlalchemy import text

from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo

from core.pipeline.data.cluster.events import DATA_CLUSTER_REQUESTED
from core.pipeline.data.normalise.repos.normalisation_article_repo import (
    NormalisationArticleRepo,
)
from core.pipeline.data.normalise.categorise.entity_service import (
    categorise_article,
)
from core.pipeline.data.normalise.categorise.category_service import (
    categorise_categories,
)
from core.pipeline.data.normalise.categorise.pool_reconcile import reconcile_with_pool
from core.pipeline.data.ingest.pool_taxonomy import geo_region as _geo_region_for


def handle_normalise_requested(event: dict) -> None:
    """
    Event: data.normalise.requested

    Responsibilities:
    - read data.ingested_articles for this ingestion_run_id
    - extract snippet
    - categorise entities
    - categorise article into taxonomy categories
    - insert clean rows into data.normalisation_articles
    - persist entity definitions + article/entity links
    - enqueue data.summarise.requested (temporary next step)
    """

    payload = event.get("payload") or {}
    ingestion_run_id = payload.get("ingestion_run_id")
    trace_id = event.get("trace_id")

    if not ingestion_run_id:
        raise ValueError("ingestion_run_id missing in normalise event payload")

    with SessionLocal() as db:
        outbox = OutboxRepo(db)
        repo = NormalisationArticleRepo(db)

        # Pool stamp for this run (pool-aware ingestion). Denormalised onto every
        # article below so topic coarse + geography are carried by construction.
        run_meta = db.execute(
            text("SELECT pool_category, pool_country FROM data.ingestion_runs WHERE id = :id"),
            {"id": ingestion_run_id},
        ).mappings().first()
        pool_category = run_meta["pool_category"] if run_meta else None
        pool_country = run_meta["pool_country"] if run_meta else None
        region = _geo_region_for(pool_country)

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

            # Idempotency guard
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
                if len(snippet) > 800:
                    snippet = snippet[:800]

            # --------------------------------------------------
            # 1) Entity categorisation
            # --------------------------------------------------
            entity_result = categorise_article(
                title=r["title"],
                content_snippet=snippet,
            )

            # --------------------------------------------------
            # 2) Category categorisation
            # --------------------------------------------------
            category_result = categorise_categories(
                title=r["title"],
                content_snippet=snippet,
                entity_slugs=entity_result.entity_slugs,
            )

            # 2b) Verification layer + politics derivation (pool coarse × our fine)
            rec_slugs, rec_primary, rec_method = reconcile_with_pool(
                category_slugs=category_result.category_slugs,
                primary_category=category_result.primary_category,
                method=category_result.method,
                pool_category=pool_category,
                region=region,
                text=f"{r['title']} {snippet or ''}",
            )

            # --------------------------------------------------
            # 3) Insert normalised article
            # --------------------------------------------------
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
                        category_version,
                        pool_category,
                        pool_country,
                        geo_region
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
                        :category_version,
                        :pool_category,
                        :pool_country,
                        :geo_region
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
                    "category_slugs": rec_slugs,
                    "category_primary": rec_primary,
                    "category_method": rec_method,
                    "category_version": category_result.version,
                    "pool_category": pool_category,
                    "pool_country": pool_country,
                    "geo_region": region,
                },
            ).scalar_one()

            # --------------------------------------------------
            # 4) Persist entities + links
            # --------------------------------------------------
            for slug in entity_result.entity_slugs:
                entity_id = repo.get_entity_id_by_slug(slug)

                if entity_id is None:
                    meta = repo.get_registry_metadata(slug)
                    entity_id = repo.insert_entity(
                        slug=slug,
                        display_name=meta["display_name"],
                        entity_type=meta["entity_type"],
                    )

                repo.link_entity_to_article(
                    normalisation_article_id=normalised_id,
                    entity_id=entity_id,
                    is_primary=(slug == entity_result.primary_entity),
                    confidence_score=float(entity_result.scores.get(slug, 1.0)),
                )

            inserted_count += 1

        next_event = Event(
            type=DATA_CLUSTER_REQUESTED,
            idempotency_key=f"cluster:{ingestion_run_id}",
            payload={
                "ingestion_run_id": ingestion_run_id,
            },
            trace_id=trace_id,
        )

        outbox.add_event(next_event)
        db.commit()