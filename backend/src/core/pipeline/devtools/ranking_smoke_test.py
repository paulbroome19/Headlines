from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.pipeline.feeds.ranking.models import StoryRankingCandidate
from core.pipeline.feeds.ranking.ranker import StoryRanker


def build_candidates(now: datetime) -> list[StoryRankingCandidate]:
    return [
        StoryRankingCandidate(
            story_id="story-1",
            title="Fed signals possible rate cut after weaker inflation data",
            last_seen_at=now - timedelta(hours=1),
            article_count=5,
            source_count=4,
            primary_category="business.markets",
            primary_entity_id="fed",
            primary_entity_name="Federal Reserve",
            primary_entity_weight=1.5,
            primary_topic_key="macro.rates",
        ),
        StoryRankingCandidate(
            story_id="story-2",
            title="Nvidia shares rise after strong AI demand forecast",
            last_seen_at=now - timedelta(hours=2),
            article_count=4,
            source_count=3,
            primary_category="business.companies",
            primary_entity_id="nvidia",
            primary_entity_name="Nvidia",
            primary_entity_weight=1.3,
            primary_topic_key="equities.ai",
        ),
        StoryRankingCandidate(
            story_id="story-3",
            title="UK inflation comes in above expectations in latest release",
            last_seen_at=now - timedelta(hours=3),
            article_count=3,
            source_count=3,
            primary_category="business.economy",
            primary_entity_id="ons",
            primary_entity_name="Office for National Statistics",
            primary_entity_weight=1.2,
            primary_topic_key="macro.inflation",
        ),
        StoryRankingCandidate(
            story_id="story-4",
            title="Premier League title race intensifies after late winner",
            last_seen_at=now - timedelta(hours=1),
            article_count=6,
            source_count=5,
            primary_category="sport.football.premier-league",
            primary_entity_id="arsenal",
            primary_entity_name="Arsenal",
            primary_entity_weight=1.0,
            primary_topic_key="sport.football",
        ),
        StoryRankingCandidate(
            story_id="story-5",
            title="Celebrity couple announces new documentary project",
            last_seen_at=now - timedelta(hours=1),
            article_count=2,
            source_count=2,
            primary_category="entertainment",
            primary_entity_id=None,
            primary_entity_name=None,
            primary_entity_weight=None,
            primary_topic_key="entertainment.celebrity",
        ),
        StoryRankingCandidate(
            story_id="story-6",
            title="Oil prices surge on renewed Middle East supply fears",
            last_seen_at=now - timedelta(hours=5),
            article_count=4,
            source_count=4,
            primary_category="world",
            primary_entity_id="brent",
            primary_entity_name="Brent Crude",
            primary_entity_weight=1.2,
            primary_topic_key="energy.oil",
        ),
        StoryRankingCandidate(
            story_id="story-7",
            title="Small local event draws modest turnout",
            last_seen_at=now - timedelta(hours=10),
            article_count=1,
            source_count=1,
            primary_category=None,
            primary_entity_id=None,
            primary_entity_name=None,
            primary_entity_weight=None,
            primary_topic_key="local.misc",
        ),
        StoryRankingCandidate(
            story_id="story-8",
            title="Fed officials split on timing of next rate move",
            last_seen_at=now - timedelta(hours=2),
            article_count=3,
            source_count=3,
            primary_category="business.markets",
            primary_entity_id="fed",
            primary_entity_name="Federal Reserve",
            primary_entity_weight=1.5,
            primary_topic_key="macro.rates",
        ),
        StoryRankingCandidate(
            story_id="story-9",
            title="US bond yields fall as traders price in easing path",
            last_seen_at=now - timedelta(hours=2),
            article_count=3,
            source_count=2,
            primary_category="business.markets",
            primary_entity_id="ust",
            primary_entity_name="US Treasuries",
            primary_entity_weight=1.2,
            primary_topic_key="macro.rates",
        ),
    ]

def main() -> None:
    now = datetime.now(timezone.utc)
    candidates = build_candidates(now)

    ranker = StoryRanker()
    ranked = ranker.rank(candidates, now=now)

    print("\nRanked stories:\n")
    for index, story in enumerate(ranked, start=1):
        print(
            f"{index:>2}. {story.title}\n"
            f"    story_id={story.story_id}\n"
            f"    base_score={story.base_score:.4f}\n"
            f"    adjusted_score={story.adjusted_score:.4f}\n"
            f"    category={story.primary_category}\n"
            f"    entity={story.primary_entity_name}\n"
            f"    recency={story.recency_score:.4f} "
            f"category_w={story.category_weight:.2f} "
            f"entity_w={story.entity_weight:.2f} "
            f"source_w={story.source_weight:.2f} "
            f"cluster_w={story.cluster_weight:.2f}\n"
        )


if __name__ == "__main__":
    main()