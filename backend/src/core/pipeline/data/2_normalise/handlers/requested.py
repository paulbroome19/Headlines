from sqlalchemy import text

from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo

from core.pipeline.data.summarise.events import DATA_SUMMARISE_REQUESTED


def handle_normalise_requested(event: dict) -> None:
    """
    Event: data.2_normalise.requested

    Responsibilities:
    - read data.ingested_articles for this ingestion_run_id
    - insert clean rows into data.normalisation_articles (expanded schema)
    - enqueue data.summarise.requested (next stage for now)
    """

    payload = event.get("payload") or {}
    ingestion_run_id = payload.get("ingestion_run_id")
    trace_id = event.get("trace_id")

    if not ingestion_run_id:
        raise ValueError("ingestion_run_id missing in 2_normalise event payload")

    with SessionLocal() as db:
        outbox = OutboxRepo(db)

        # 1) Fetch ingested rows for this run (including expanded metadata)
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
            # Skip malformed rows
            if not r["title"] or not r["url"]:
                continue

            # Prevent duplicates if event replays / retry happens
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
            # Keep this small + cheap for now. (We can improve later.)
            snippet = raw.get("description") or raw.get("content") or None
            if isinstance(snippet, str):
                snippet = snippet.strip()
                if snippet == "":
                    snippet = None
                # hard cap to keep rows tidy
                if snippet and len(snippet) > 800:
                    snippet = snippet[:800]

            db.execute(
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
                        status
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
                        'normalised'
                    )
                    """
                ),
                {
                    "ingestion_run_id": ingestion_run_id,
                    "ingested_article_id": r["id"],
                    "provider": r["provider"] or "unknown",
                    "title": r["title"],
                    "url": r["url"],
                    "source": r["publisher"] or "unknown",  # this is the BRAND the user sees
                    "published_at": r["published_at"],
                    "content_snippet": snippet,
                },
            )

            inserted_count += 1

        # 3) Enqueue next stage (temporary: summarise)
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