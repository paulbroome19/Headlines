from __future__ import annotations

import json

from core.pipeline.ranking.repos.ranking_run_repo import RankingRunRepo
from core.platform.db.session import SessionLocal


def _print_story(index: int, story: dict) -> None:
    print(
        f"{index:>2}. {story.get('title', '(no title)')}\n"
        f"    story_id={story.get('story_id')}  category={story.get('primary_category')}\n"
        f"    full_score={story.get('full_score', 0):.4f}  "
        f"adjusted_score={story.get('adjusted_score', 0):.4f}  "
        f"within_category={story.get('within_category_score', 0):.4f}\n"
        f"    entity={story.get('primary_entity_name')}  topic={story.get('primary_topic_key')}\n"
        f"    recency={story.get('recency_score', 0):.4f}  "
        f"category_w={story.get('category_weight', 0):.2f}  "
        f"entity_w={story.get('entity_weight', 0):.2f}  "
        f"source_w={story.get('source_weight', 0):.2f}  "
        f"cluster_w={story.get('cluster_weight', 0):.2f}\n"
    )


def main() -> None:
    with SessionLocal() as db:
        repo = RankingRunRepo(db)
        run = repo.get_latest()

    if not run:
        print("No ranking runs found in data.ranking_runs.")
        return

    top_stories = run.get("top_stories") or []
    briefing = run.get("briefing") or []

    if isinstance(top_stories, str):
        top_stories = json.loads(top_stories)
    if isinstance(briefing, str):
        briefing = json.loads(briefing)

    print(f"\nLatest ranking run")
    print(f"  id            : {run['id']}")
    print(f"  batch_id      : {run['batch_id']}")
    print(f"  candidate_count: {run['candidate_count']}")
    print(f"  created_at    : {run['created_at']}")
    print()

    print(f"Top stories ({len(top_stories)}):\n")
    for i, story in enumerate(top_stories, start=1):
        _print_story(i, story)

    print(f"Briefing ({len(briefing)}):\n")
    for i, story in enumerate(briefing, start=1):
        _print_story(i, story)

    if not top_stories and not briefing:
        print("Run exists but both top_stories and briefing are empty.")


if __name__ == "__main__":
    main()
