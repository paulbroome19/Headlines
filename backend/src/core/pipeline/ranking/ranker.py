from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .category_ranker import get_category_weight
from .models import RankingResult, StoryRankingCandidate
from .scorer import score_story
from .selection import select_briefing, select_top_stories


class StoryRanker:
    """
    Thin orchestrator: scorer → category_ranker → selection.

    Produces two outputs via RankingResult:

    top_stories — globally highest-scoring stories, no category filtering,
                  no diversity penalties. Suitable for a "breaking news" shelf.

    briefing    — filtered to user's selected categories, diminishing returns
                  applied, top_stories excluded. The main personalised bulletin.
    """

    def rank(
        self,
        candidates: Iterable[StoryRankingCandidate],
        *,
        user_categories: list[str] | None = None,
        top_stories_limit: int = 5,
        briefing_limit: int = 10,
        now: datetime | None = None,
    ) -> RankingResult:
        scored = [
            score_story(c, get_category_weight(c.primary_category), now=now)
            for c in candidates
        ]

        top_stories = select_top_stories(scored, limit=top_stories_limit)
        top_story_ids = {s.story_id for s in top_stories}

        briefing = select_briefing(
            scored,
            user_categories=user_categories,
            exclude_story_ids=top_story_ids,
            limit=briefing_limit,
        )

        return RankingResult(top_stories=top_stories, briefing=briefing)
