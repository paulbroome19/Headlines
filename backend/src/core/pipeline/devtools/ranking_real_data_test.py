from __future__ import annotations

from datetime import datetime, timezone

from core.pipeline.ranking.candidate_loader import load_story_ranking_candidates
from core.pipeline.ranking.ranker import StoryRanker
from core.platform.db.session import SessionLocal


def _print_story(index: int, story) -> None:
    print(
        f"{index:>2}. {story.title}\n"
        f"    story_id={story.story_id}\n"
        f"    full_score={story.full_score:.4f}  adjusted_score={story.adjusted_score:.4f}\n"
        f"    within_category={story.within_category_score:.4f}  category={story.primary_category}\n"
        f"    entity={story.primary_entity_name}  topic={story.primary_topic_key}\n"
        f"    recency={story.recency_score:.4f} "
        f"category_w={story.category_weight:.2f} "
        f"entity_w={story.entity_weight:.2f} "
        f"source_w={story.source_weight:.2f} "
        f"cluster_w={story.cluster_weight:.2f}\n"
    )


def main() -> None:
    now = datetime.now(timezone.utc)

    with SessionLocal() as session:
        candidates = load_story_ranking_candidates(session, hours=48)

    print(f"\nLoaded {len(candidates)} candidates\n")

    if not candidates:
        print("No candidates found. Is the pipeline running?\n")
        return

    ranker = StoryRanker()
    result = ranker.rank(candidates, now=now, top_stories_limit=5, briefing_limit=15)

    print(f"Top stories ({len(result.top_stories)}):\n")
    for i, story in enumerate(result.top_stories, start=1):
        _print_story(i, story)

    print(f"Briefing ({len(result.briefing)}):\n")
    for i, story in enumerate(result.briefing, start=1):
        _print_story(i, story)

    if not result.top_stories and not result.briefing:
        print(
            "No stories passed selection. Candidates may be too old for the current "
            "recency/threshold settings.\n"
        )
        print("First 10 candidates before ranking:\n")
        for i, c in enumerate(candidates[:10], start=1):
            print(
                f"{i:>2}. {c.title}\n"
                f"    story_id={c.story_id}  category={c.primary_category}\n"
                f"    topic={c.primary_topic_key}  last_seen_at={c.last_seen_at.isoformat()}\n"
                f"    article_count={c.article_count}  source_count={c.source_count}\n"
            )


if __name__ == "__main__":
    main()
