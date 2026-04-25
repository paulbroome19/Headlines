import re

from sqlalchemy import text

from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo

from core.pipeline.data.cluster.events import DATA_CLUSTER_COMPLETED


def _normalize_title(title: str) -> str:
    return (title or "").lower().strip()


_HINT_STOPWORDS: frozenset[str] = frozenset({
    # grammar / prepositions
    'a', 'an', 'the', 'and', 'or', 'but', 'not', 'nor',
    'of', 'in', 'on', 'at', 'to', 'for', 'with', 'from', 'by',
    'as', 'into', 'over', 'under', 'after', 'before', 'since',
    'about', 'against', 'between', 'through', 'during', 'within',
    # auxiliaries
    'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'has', 'have', 'had', 'does', 'did', 'will', 'would',
    'could', 'should', 'shall', 'may', 'might',
    # reporting verbs
    'says', 'said', 'tells', 'told', 'claims', 'claimed',
    'warns', 'warned', 'reveals', 'revealed', 'confirms',
    # pronouns / determiners
    'it', 'its', 'he', 'she', 'they', 'his', 'her', 'their',
    'this', 'that', 'these', 'those', 'who', 'which', 'what',
    'how', 'why', 'when', 'where',
    # generic headline words
    'new', 'big', 'top', 'key', 'more', 'first', 'last', 'next',
    'live', 'breaking', 'exclusive', 'latest', 'alert',
    # generic news subjects
    'man', 'men', 'woman', 'women', 'person', 'people', 'family',
    # legal / crime jargon too generic
    'police', 'court', 'judge', 'jury', 'lawyers', 'attorney',
    'minister', 'ministers', 'government', 'official', 'officials',
    'report', 'reports', 'warning', 'update', 'review',
    'investigation', 'inquiry', 'probe', 'charges', 'verdict',
    'hearing', 'trial', 'case', 'ruling', 'decision', 'inquest',
    'arrested', 'arrest', 'detained', 'suspected', 'suspicion',
    'accused', 'charged', 'convicted', 'sentenced', 'murdered',
    'murder', 'killed', 'death', 'died',
    # connector / action words too generic
    'connection', 'connected', 'linked', 'links', 'link',
    'raid', 'raids', 'raided',
    # death / generic outcome words
    'dies', 'dead', 'death', 'died', 'killed',
    # generic location/building words
    'flat', 'home', 'house', 'area', 'region', 'town', 'city',
    # generic article/media words
    'news', 'story', 'claim', 'claims',
    # short ambiguous
    'uk', 'us', 'now', 'here', 'just', 'also', 'only',
    'than', 'each', 'both', 'even', 'well', 'much', 'many',
})


def _extract_title_hint(title: str) -> str | None:
    """Return the first non-stopword token from title (>=4 chars), or None.

    Used only when an article has no primary entity. Combined with category,
    it forms a two-condition dedup key so unrelated stories sharing a common
    word (e.g. "hamilton" in sport vs finance) do not incorrectly merge.
    """
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (title or "").lower())
    for word in cleaned.split():
        if len(word) >= 4 and word not in _HINT_STOPWORDS:
            return word
    return None


def handle_cluster_requested(event: dict) -> None:
    payload = event.get("payload") or {}
    ingestion_run_id = payload.get("ingestion_run_id")
    trace_id = event.get("trace_id")

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

        affected_story_ids: set[int] = set()

        for r in rows:
            article_id = r["id"]
            title = r["title"]
            category = r["category_primary"]
            published_at = r["published_at"]

            # --------------------------------------------------
            # Get primary entity
            # --------------------------------------------------
            entity_row = db.execute(
                text(
                    """
                    SELECT e.slug, e.entity_type
                    FROM data.normalisation_article_entities nae
                    JOIN data.entities e ON e.id = nae.entity_id
                    WHERE nae.normalisation_article_id = :id
                      AND nae.is_primary = TRUE
                    LIMIT 1
                    """
                ),
                {"id": article_id},
            ).mappings().one_or_none()

            entity = entity_row["slug"] if entity_row else None
            entity_type = entity_row["entity_type"] if entity_row else None

            # Countries match too many unrelated articles (any article that
            # mentions a country in passing gets that entity). Using a country
            # as the sole dedup key would merge gaming news, diplomacy, and
            # crime articles just because they all say "United States".
            # Specific orgs, teams, persons, and indexes are narrow enough to
            # anchor a story. Countries are not.
            is_clustering_anchor = entity is not None and entity_type != "country"

            normalized_title = _normalize_title(title)

            # --------------------------------------------------
            # Try find existing story
            # --------------------------------------------------
            story_id = None

            if is_clustering_anchor:
                # Entity-based dedup: same specific entity within 24h → same story.
                # Only non-country entities qualify (see is_clustering_anchor above).
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

            elif category:
                # Hint-based dedup: same leading hint word AND same category within 24h.
                # Two conditions prevent unrelated stories sharing a common word from merging
                # (e.g. "hamilton" in sport.formula1 vs business.markets never merge).
                # Country-entity articles and entity-less articles both reach this branch:
                # country entities are too broad to anchor a story on their own.
                hint = _extract_title_hint(title)
                if hint:
                    hint_prefix = f"hint:{hint}:{category}|"
                    story = db.execute(
                        text(
                            """
                            SELECT id
                            FROM data.stories
                            WHERE story_key LIKE :prefix
                              AND primary_category = :category
                              AND last_published_at > NOW() - INTERVAL '24 hours'
                            ORDER BY last_published_at DESC
                            LIMIT 1
                            """
                        ),
                        {"prefix": hint_prefix + "%", "category": category},
                    ).scalar_one_or_none()
                    if story:
                        story_id = story

            # --------------------------------------------------
            # Derive story_key for new stories
            # --------------------------------------------------
            if is_clustering_anchor:
                story_key = f"{entity}|{normalized_title}"
            elif category and (hint := _extract_title_hint(title)):
                story_key = f"hint:{hint}:{category}|{normalized_title}"
            else:
                story_key = f"none|{normalized_title}"

            # --------------------------------------------------
            # Update or create story
            # --------------------------------------------------
            if story_id:
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
                        "story_key": story_key,
                        "entity": entity,
                        "category": category,
                        "title": title,
                        "published_at": published_at,
                    },
                ).scalar_one()

            affected_story_ids.add(story_id)

            # --------------------------------------------------
            # Link article → story
            # --------------------------------------------------
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

            # --------------------------------------------------
            # Update article
            # --------------------------------------------------
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

        # --------------------------------------------------
        # Emit data.cluster.completed
        # --------------------------------------------------
        outbox = OutboxRepo(db)
        outbox.add_event(Event(
            type=DATA_CLUSTER_COMPLETED,
            idempotency_key=f"cluster.completed:{ingestion_run_id}",
            payload={
                "story_ids": list(affected_story_ids),
                "cluster_batch_id": ingestion_run_id,
                "ingestion_run_id": ingestion_run_id,
            },
            trace_id=trace_id,
        ))

        db.commit()
