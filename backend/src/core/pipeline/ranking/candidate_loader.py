from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

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
            return "world.misc"

        if bucket == "politics":
            return "politics.misc"

        if bucket == "sport":
            return "sport.misc"

        if bucket == "entertainment":
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
        )
        SELECT
            sas.story_id,
            rep.title,
            sas.last_seen_at,
            sas.article_count,
            sas.source_count,
            rep.category_primary AS primary_category,
            NULL::text AS primary_entity_id,
            NULL::text AS primary_entity_name,
            NULL::double precision AS primary_entity_weight
        FROM story_article_stats sas
        JOIN representative_articles rep
            ON rep.story_id = sas.story_id
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
        primary_entity_weight = row["primary_entity_weight"]

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
                primary_entity_weight=float(primary_entity_weight)
                if primary_entity_weight is not None
                else None,
                primary_topic_key=derive_primary_topic_key(
                    primary_category=primary_category,
                    primary_entity_name=primary_entity_name,
                    title=title,
                ),
            )
        )

    return candidates