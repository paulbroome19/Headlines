from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import ENTITY_TYPE_WEIGHTS
from .models import StoryRankingCandidate


def slugify_tokens(value: str) -> list[str]:
    cleaned = (
        value.strip()
        .lower()
        .replace("&", " and ")
        .replace("'", "")
        .replace('"', "")
        .replace(".", " ")
        .replace(",", " ")
        .replace("-", " ")
        .replace("/", " ")
    )
    return [token for token in cleaned.split() if token]


def slugify(value: str) -> str:
    return ".".join(slugify_tokens(value))


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def derive_entity_weight(entity_type: str | None) -> float | None:
    if not entity_type:
        return None
    return ENTITY_TYPE_WEIGHTS.get(entity_type.lower(), 1.00)


def derive_primary_topic_key(
    *,
    primary_category: str | None,
    primary_entity_name: str | None,
    title: str,
) -> str:
    title_lower = title.lower()

    if primary_category:
        bucket = primary_category.split(".")[0]

        if bucket == "business":
            if "inflation" in title_lower:
                return "macro.inflation"

            if (
                "rate" in title_lower
                or "rates" in title_lower
                or "yield" in title_lower
                or "yields" in title_lower
                or "fed" in title_lower
                or "central bank" in title_lower
            ):
                return "macro.rates"

            if "oil" in title_lower or "crude" in title_lower:
                return "energy.oil"

            if "ai" in title_lower:
                return "equities.ai"

            if primary_entity_name:
                return f"business.entity.{slugify(primary_entity_name)}"

            return "business.misc"

        if bucket == "technology":
            if "ai" in title_lower:
                return "technology.ai"
            return "technology.misc"

        if bucket == "world":
            if "oil" in title_lower or "crude" in title_lower:
                return "energy.oil"
            if primary_entity_name:
                return f"world.entity.{slugify(primary_entity_name)}"
            return "world.misc"

        if bucket == "politics":
            if primary_entity_name:
                return f"politics.entity.{slugify(primary_entity_name)}"
            return "politics.misc"

        if bucket == "sport":
            if primary_entity_name:
                return f"sport.entity.{slugify(primary_entity_name)}"
            return "sport.misc"

        if bucket == "entertainment":
            if primary_entity_name:
                return f"entertainment.entity.{slugify(primary_entity_name)}"
            return "entertainment.misc"

        return f"{bucket}.misc"

    if primary_entity_name:
        return f"uncategorised.entity.{slugify(primary_entity_name)}"

    return "uncategorised.misc"


def load_story_ranking_candidates(
    session: Session,
    *,
    hours: int = 48,
) -> list[StoryRankingCandidate]:
    """
    Load recent story candidates for ranking directly from normalisation_articles,
    using story_id on that table as the source of truth.

    This version also derives a story-level primary entity from
    data.normalisation_article_entities + data.entities.
    """
    sql = text(
        """
        WITH recent_articles AS (
            SELECT
                na.id AS article_id,
                na.story_id,
                na.title,
                na.category_primary,
                na.source,
                na.published_at
            FROM data.normalisation_articles na
            WHERE na.story_id IS NOT NULL
              AND na.published_at IS NOT NULL
              AND na.published_at >= (NOW() AT TIME ZONE 'UTC') - (:hours || ' hours')::interval
        ),
        story_article_stats AS (
            SELECT
                ra.story_id,
                COUNT(*) AS article_count,
                COUNT(DISTINCT ra.source) AS source_count,
                MAX(ra.published_at) AS last_seen_at
            FROM recent_articles ra
            GROUP BY ra.story_id
        ),
        representative_articles AS (
            SELECT DISTINCT ON (ra.story_id)
                ra.story_id,
                ra.article_id,
                ra.title,
                ra.category_primary
            FROM recent_articles ra
            ORDER BY ra.story_id, ra.published_at DESC, ra.article_id DESC
        ),
        story_entities AS (
            SELECT
                ra.story_id,
                e.id AS entity_id,
                e.display_name,
                e.entity_type,
                MAX(CASE WHEN nae.is_primary THEN 1 ELSE 0 END) AS has_primary,
                MAX(COALESCE(nae.confidence_score, 0)) AS max_confidence,
                COUNT(*) AS mention_count
            FROM recent_articles ra
            JOIN data.normalisation_article_entities nae
                ON nae.normalisation_article_id = ra.article_id
            JOIN data.entities e
                ON e.id = nae.entity_id
            GROUP BY
                ra.story_id,
                e.id,
                e.display_name,
                e.entity_type
        ),
        primary_story_entities AS (
            SELECT DISTINCT ON (se.story_id)
                se.story_id,
                se.entity_id,
                se.display_name,
                se.entity_type,
                se.has_primary,
                se.max_confidence,
                se.mention_count
            FROM story_entities se
            ORDER BY
                se.story_id,
                se.has_primary DESC,
                se.max_confidence DESC,
                se.mention_count DESC,
                se.entity_id ASC
        )
        SELECT
            sas.story_id,
            rep.title,
            sas.last_seen_at,
            sas.article_count,
            sas.source_count,
            rep.category_primary AS primary_category,
            pse.entity_id AS primary_entity_id,
            pse.display_name AS primary_entity_name,
            pse.entity_type AS primary_entity_type
        FROM story_article_stats sas
        JOIN representative_articles rep
            ON rep.story_id = sas.story_id
        LEFT JOIN primary_story_entities pse
            ON pse.story_id = sas.story_id
        ORDER BY sas.last_seen_at DESC
        """
    )

    rows = session.execute(sql, {"hours": hours}).mappings().all()

    candidates: list[StoryRankingCandidate] = []

    for row in rows:
        title = row["title"] or "Untitled story"
        primary_category = row["primary_category"]
        primary_entity_id = row["primary_entity_id"]
        primary_entity_name = row["primary_entity_name"]
        primary_entity_type = row["primary_entity_type"]

        candidates.append(
            StoryRankingCandidate(
                story_id=str(row["story_id"]),
                title=title,
                last_seen_at=ensure_utc(row["last_seen_at"]),
                article_count=int(row["article_count"] or 0),
                source_count=int(row["source_count"] or 0),
                primary_category=primary_category,
                primary_entity_id=str(primary_entity_id)
                if primary_entity_id is not None
                else None,
                primary_entity_name=primary_entity_name,
                primary_entity_weight=derive_entity_weight(primary_entity_type),
                primary_topic_key=derive_primary_topic_key(
                    primary_category=primary_category,
                    primary_entity_name=primary_entity_name,
                    title=title,
                ),
            )
        )

    return candidates