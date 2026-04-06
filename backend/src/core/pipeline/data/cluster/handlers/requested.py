from sqlalchemy import text

from core.platform.db.session import SessionLocal


def _normalize_title(title: str) -> str:
    return (title or "").lower().strip()


def handle_cluster_requested(event: dict) -> None:
    payload = event.get("payload") or {}
    ingestion_run_id = payload.get("ingestion_run_id")

    if not ingestion_run_id:
        raise ValueError("missing ingestion_run_id")

    with SessionLocal() as db:

        rows = db.execute(
            text(
                """
                SELECT
                    na.id,
                    na.title,
                    na.category_primary,
                    na.published_at
                FROM data.normalisation_articles na
                WHERE na.ingestion_run_id = :ingestion_run_id
                ORDER BY na.id ASC
                """
            ),
            {"ingestion_run_id": ingestion_run_id},
        ).mappings().all()

        for r in rows:
            article_id = r["id"]
            title = r["title"]
            category = r["category_primary"]
            published_at = r["published_at"]

            # Get primary entity
            entity = db.execute(
                text(
                    """
                    SELECT e.slug
                    FROM data.normalisation_article_entities nae
                    JOIN data.entities e ON e.id = nae.entity_id
                    WHERE nae.normalisation_article_id = :id
                      AND nae.is_primary = TRUE
                    LIMIT 1
                    """
                ),
                {"id": article_id},
            ).scalar_one_or_none()

            normalized_title = _normalize_title(title)

            # Try find existing story
            story = db.execute(
                text(
                    """
                    SELECT id
                    FROM data.stories
                    WHERE primary_entity_slug = :entity
                      AND last_published_at > NOW() - INTERVAL '24 hours'
                    ORDER BY last_published_at DESC
                    LIMIT 1
                    """
                ),
                {"entity": entity},
            ).scalar_one_or_none()

            if story:
                story_id = story

                # update story
                db.execute(
                    text(
                        """
                        UPDATE data.stories
                        SET
                            last_published_at = GREATEST(last_published_at, :published_at),
                            article_count = article_count + 1
                        WHERE id = :id
                        """
                    ),
                    {"id": story_id, "published_at": published_at},
                )

            else:
                # create new story
                story_id = db.execute(
                    text(
                        """
                        INSERT INTO data.stories (
                            story_key,
                            primary_entity_slug,
                            primary_category,
                            representative_title,
                            first_published_at,
                            last_published_at,
                            article_count
                        )
                        VALUES (
                            :story_key,
                            :entity,
                            :category,
                            :title,
                            :published_at,
                            :published_at,
                            1
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "story_key": f"{entity}|{normalized_title}",
                        "entity": entity,
                        "category": category,
                        "title": title,
                        "published_at": published_at,
                    },
                ).scalar_one()

            # link article → story
            db.execute(
                text(
                    """
                    INSERT INTO data.story_articles (
                        story_id,
                        normalisation_article_id,
                        is_primary
                    )
                    VALUES (
                        :story_id,
                        :article_id,
                        TRUE
                    )
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "story_id": story_id,
                    "article_id": article_id,
                },
            )

            # update article
            db.execute(
                text(
                    """
                    UPDATE data.normalisation_articles
                    SET story_id = :story_id
                    WHERE id = :id
                    """
                ),
                {"story_id": story_id, "id": article_id},
            )

        db.commit()