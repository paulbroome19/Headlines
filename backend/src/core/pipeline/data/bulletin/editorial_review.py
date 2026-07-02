"""
Editorial pass orchestration: build the review inputs, call the editor (cached per
ranking run), and APPLY the bounded adjustments to the scored candidate set before
selection. Cached by ranking_run_id so the single review is reused across every user,
filter and regeneration of that run — one cheap call per ranking run, not per bulletin.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.platform.config.settings import settings
from core.pipeline.data.normalise.categorise.category_loader import load_valid_category_slugs
from core.pipeline.ranking.config import TOP_STORIES_REGIONS
from core.pipeline.ranking.models import ScoredStory
from .editorial import (
    MAX_PROMOTIONS, EditorialAdjustment, StoryMeta, _model, review_front_page,
)

logger = logging.getLogger(__name__)

# How many top-ranked candidates the editor reviews. Bounds cost and scope: a story below
# this never surfaces even in the deepest bulletin (Deep max 18), so reviewing the top
# slice covers everything that can go out, and the long tail is never paid for.
REVIEW_N = 40

_DDL = """
CREATE TABLE IF NOT EXISTS data.editorial_reviews (
    id               BIGSERIAL PRIMARY KEY,
    ranking_run_id   BIGINT NOT NULL,
    story_id         TEXT NOT NULL,
    category         TEXT,
    significance     REAL NOT NULL DEFAULT 0,
    reason           TEXT,
    model            TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ranking_run_id, story_id)
);
"""
_MARKER = "__reviewed__"   # sentinel row: this run was reviewed (even if 0 changes)
_ensured = False


class EditorialRepo:
    def __init__(self, db: Session) -> None:
        self.db = db

    def ensure_table(self) -> None:
        global _ensured
        if _ensured:
            return
        self.db.execute(text(_DDL))
        self.db.commit()
        _ensured = True

    def get(self, ranking_run_id: int) -> dict[str, EditorialAdjustment] | None:
        rows = self.db.execute(
            text("""SELECT story_id, category, significance, reason
                    FROM data.editorial_reviews WHERE ranking_run_id = :r"""),
            {"r": ranking_run_id},
        ).all()
        if not rows:
            return None  # not yet reviewed
        out: dict[str, EditorialAdjustment] = {}
        for sid, cat, sig, reason in rows:
            if sid == _MARKER:
                continue
            out[sid] = EditorialAdjustment(story_id=sid, category=cat,
                                           significance_delta=sig or 0.0, reason=reason or "")
        return out

    def put(self, ranking_run_id: int, adjustments: dict[str, EditorialAdjustment], model: str) -> None:
        rows = [(_MARKER, None, 0.0, "")]  # always mark the run reviewed
        rows += [(a.story_id, a.category, a.significance_delta, a.reason) for a in adjustments.values()]
        for sid, cat, sig, reason in rows:
            self.db.execute(
                text("""INSERT INTO data.editorial_reviews
                            (ranking_run_id, story_id, category, significance, reason, model)
                        VALUES (:r, :s, :c, :sig, :reason, :m)
                        ON CONFLICT (ranking_run_id, story_id) DO NOTHING"""),
                {"r": ranking_run_id, "s": sid, "c": cat, "sig": sig, "reason": reason, "m": model},
            )
        self.db.commit()


def _snippets(db: Session, story_ids: list[str]) -> dict[str, str]:
    if not story_ids:
        return {}
    rows = db.execute(
        text("""
            SELECT DISTINCT ON (na.story_id) na.story_id::text, na.content_snippet
            FROM data.normalisation_articles na
            WHERE na.story_id::text = ANY(:ids)
            ORDER BY na.story_id, na.published_at DESC NULLS LAST, na.id DESC
        """),
        {"ids": story_ids},
    ).all()
    return {r[0]: (r[1] or "") for r in rows}


def _build_meta(db: Session, top: list[ScoredStory]) -> list[StoryMeta]:
    snips = _snippets(db, [s.candidate.story_id for s in top])
    return [
        StoryMeta(
            story_id=s.candidate.story_id,
            headline=s.candidate.title,
            snippet=snips.get(s.candidate.story_id, ""),
            category=s.candidate.primary_category,
            sources=s.candidate.source_count,
            rank=i + 1,
        )
        for i, s in enumerate(top)
    ]


def editorial_adjustments(
    db: Session, ranking_run_id: int, scored: list[ScoredStory],
) -> dict[str, EditorialAdjustment]:
    """The review for this ranking run (cached). Empty dict if disabled/unavailable."""
    if not settings.enable_editorial_pass or not settings.anthropic_api_key:
        return {}
    repo = EditorialRepo(db)
    repo.ensure_table()
    cached = repo.get(ranking_run_id)
    if cached is not None:
        return cached
    top = sorted(scored, key=lambda s: s.normalized_score, reverse=True)[:REVIEW_N]
    adjustments = review_front_page(_build_meta(db, top), sorted(load_valid_category_slugs()))
    repo.put(ranking_run_id, adjustments, _model())
    return adjustments


def _top_story_target(region: str | None) -> str:
    return f"top-stories.{region}" if region in TOP_STORIES_REGIONS else "top-stories"


def apply_editorial(scored: list[ScoredStory], adjustments: dict[str, EditorialAdjustment]) -> int:
    """Mutate the scored candidates in place with the bounded adjustments. Enforces the
    net-promotion cap. Returns the number of stories actually changed."""
    if not adjustments:
        return 0
    by_id = {s.candidate.story_id: s for s in scored}

    # Enforce MAX_PROMOTIONS: a promotion = an adjustment whose category moves a story ONTO
    # the front page it wasn't on. Keep the strongest (by significance, then score).
    def _is_promo(sid: str, a: EditorialAdjustment) -> bool:
        s = by_id.get(sid)
        cur = (s.candidate.primary_category or "") if s else ""
        return bool(a.category) and a.category.startswith("top-stories") and not cur.startswith("top-stories")

    promos = [sid for sid, a in adjustments.items() if sid in by_id and _is_promo(sid, a)]
    promos.sort(key=lambda sid: (adjustments[sid].significance_delta, by_id[sid].normalized_score), reverse=True)
    blocked = set(promos[MAX_PROMOTIONS:])

    changed = 0
    for sid, a in adjustments.items():
        s = by_id.get(sid)
        if s is None:
            continue
        touched = False
        if a.category and not (sid in blocked and a.category.startswith("top-stories")):
            if a.category != s.candidate.primary_category:
                s.candidate.primary_category = a.category
                touched = True
        if a.significance_delta:
            s.normalized_score = max(0.0, min(10.0, s.normalized_score + a.significance_delta))
            touched = True
        if touched:
            changed += 1
    return changed
