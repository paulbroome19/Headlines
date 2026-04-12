from __future__ import annotations

from datetime import datetime, timezone

from core.pipeline.ranking.candidate_loader import load_story_ranking_candidates
from core.pipeline.ranking.ranker import StoryRanker
from core.platform.db.session import SessionLocal


def main() -> None:
    now = datetime.now(timezone.utc)

    with SessionLocal() as session:
        candidates = load_story_ranking_candidates(session, hours=48)

    print(f"\nLoaded {len(candidates)} candidates\n")

    ranker = StoryRanker()
    ranked = ranker.rank(candidates, now=now, limit=20)

    print(f"Ranked {len(ranked)} stories\n")
    print("Top ranked real stories:\n")

    for index, story in enumerate(ranked, start=1):
        print(
            f"{index:>2}. {story.title}\n"
            f"    story_id={story.story_id}\n"
            f"    base_score={story.base_score:.4f}\n"
            f"    adjusted_score={story.adjusted_score:.4f}\n"
            f"    category={story.primary_category}\n"
            f"    entity={story.primary_entity_name}\n"
            f"    topic={story.primary_topic_key}\n"
            f"    recency={story.recency_score:.4f} "
            f"category_w={story.category_weight:.2f} "
            f"entity_w={story.entity_weight:.2f} "
            f"source_w={story.source_weight:.2f} "
            f"cluster_w={story.cluster_weight:.2f}\n"
        )

    if not ranked and candidates:
        print(
            "No stories passed selection. Most likely all candidates are too old "
            "for the current recency/threshold settings.\n"
        )
        print("First 10 loaded candidates before selection:\n")

        for index, candidate in enumerate(candidates[:10], start=1):
            print(
                f"{index:>2}. {candidate.title}\n"
                f"    story_id={candidate.story_id}\n"
                f"    category={candidate.primary_category}\n"
                f"    topic={candidate.primary_topic_key}\n"
                f"    last_seen_at={candidate.last_seen_at.isoformat()}\n"
                f"    article_count={candidate.article_count} "
                f"source_count={candidate.source_count}\n"
            )


if __name__ == "__main__":
    main()