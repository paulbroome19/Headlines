import re

from sqlalchemy import text

from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo

from core.pipeline.data.cluster.events import DATA_CLUSTER_COMPLETED
from core.pipeline.data.normalise.categorise.entity_loader import load_entity_registry

# How long a cluster may keep absorbing articles, measured from its FIRST article. This is a hard
# age cap (unlike the sliding last_published_at recency check, which extends every match and let a
# continuously-covered entity blob for days — audit risk #2). It aligns cluster lifetime with the
# ~36h ranking candidate window: nothing needs to keep growing longer than it can be selected.
_CLUSTER_MAX_AGE_HOURS = 48
_CLUSTER_RECENCY_HOURS = 24


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

    Used when an article has no ANCHOR-eligible primary entity. Combined with category,
    it forms a two-condition dedup key so unrelated stories sharing a common
    word (e.g. "hamilton" in sport vs finance) do not incorrectly merge.
    """
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (title or "").lower())
    for word in cleaned.split():
        if len(word) >= 4 and word not in _HINT_STOPWORDS:
            return word
    return None


def is_clustering_anchor(entity: str | None, entity_type: str | None, anchor_eligible: bool) -> bool:
    """Whether a story may anchor on this entity's slug ALONE. Countries match too many unrelated
    articles; broad institutions/venues flagged `anchor_eligible: false` in entities.yml (nato,
    white-house…) generate continuous unrelated coverage and would collapse distinct events into
    one mega-cluster. Both fall to the narrower hint path instead. Specific orgs / teams /
    companies / persons / indexes stay anchors."""
    return entity is not None and entity_type != "country" and anchor_eligible


def _top_category(category: str | None) -> str | None:
    """Top-level slug (politics.us -> politics). Anchor matches are gated on this so one entity
    can no longer merge across topics (white-house culture vs sport vs world). Top-level, not the
    full slug, so it stays stable against sub-category categoriser noise."""
    return category.split(".")[0] if category else None


def cluster_bucket(*, entity, entity_type, anchor_eligible, category, title) -> str:
    """Canonical merge identity: two articles WITHIN the age window join the same cluster iff they
    share this bucket. Anchors bucket on (entity, top-level-category); demoted / entity-less
    articles bucket on (first-title-word, category) — the hint path; the rest on the normalized
    title. This mirrors the match conditions + key derivation in handle_cluster_requested below,
    and is the unit-tested spec for them."""
    if is_clustering_anchor(entity, entity_type, anchor_eligible):
        return f"entity:{entity}:{_top_category(category) or ''}"
    hint = _extract_title_hint(title)
    if category and hint:
        return f"hint:{hint}:{category}"
    return f"none:{_normalize_title(title)}"


def handle_cluster_requested(event: dict) -> None:
    payload = event.get("payload") or {}
    ingestion_run_id = payload.get("ingestion_run_id")
    trace_id = event.get("trace_id")

    if not ingestion_run_id:
        raise ValueError("missing ingestion_run_id")

    # Load the entity registry ONCE per run (not per article) for the anchor-eligibility flag.
    registry = load_entity_registry()

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

            # Countries match too many unrelated articles; broad institutions/venues flagged
            # anchor_eligible=false in entities.yml (nato, white-house…) do the same. Both are too
            # broad to be a sole cluster key, so they fall to the narrower hint path. Specific
            # orgs / teams / persons / companies / indexes anchor a story. (See is_clustering_anchor.)
            meta = registry.slug_map.get(entity) if entity else None
            anchor_eligible = meta.anchor_eligible if meta is not None else True
            anchoring = is_clustering_anchor(entity, entity_type, anchor_eligible)
            top_category = _top_category(category)

            normalized_title = _normalize_title(title)

            # --------------------------------------------------
            # Try find existing story
            # --------------------------------------------------
            story_id = None

            if anchoring:
                # Entity-based dedup: same specific entity → same story, but bounded so a
                # high-frequency entity can't mega-blob (audit risk #2):
                #   - first_published_at cap (48h): a HARD age limit from the cluster's birth, so
                #     it rolls over to a fresh, coherent cluster instead of sliding for days;
                #   - last_published_at recency (24h): don't attach to a stale cluster;
                #   - top-level category gate: one entity no longer merges across topics
                #     (e.g. a sports comment vs a diplomacy story under the same org).
                story = db.execute(
                    text(
                        """
                        SELECT id
                        FROM data.stories
                        WHERE primary_entity_slug = :entity
                          AND first_published_at > NOW() - make_interval(hours => :max_age)
                          AND last_published_at  > NOW() - make_interval(hours => :recency)
                          AND (:top_category IS NULL
                               OR split_part(primary_category, '.', 1) = :top_category)
                        ORDER BY last_published_at DESC
                        LIMIT 1
                        """
                    ),
                    {"entity": entity, "max_age": _CLUSTER_MAX_AGE_HOURS,
                     "recency": _CLUSTER_RECENCY_HOURS, "top_category": top_category},
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
            if anchoring:
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
