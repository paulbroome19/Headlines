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

from collections import defaultdict

from core.platform.config.settings import settings
from core.pipeline.data.normalise.categorise.category_loader import load_valid_category_slugs
from core.pipeline.ranking.category_ranker import get_category_tier
from core.pipeline.ranking.models import ScoredStory
from .editorial import (
    MAX_PROMOTIONS, EditorialAdjustment, StoryMeta, _model, review_front_page,
)

logger = logging.getLogger(__name__)

# Review depth LADDERED BY IMPORTANCE (newspaper tier), not a flat top-N — attention/cost
# concentrates where it matters. Depth is PER CATEGORY:
#   Tier 1 (top-stories/politics/business): top ~50 each, grouped by top-level.
#   Tier 2 (world/health/science/environment): top ~20 each, grouped by top-level.
#   Tier 3 (tech/sport/culture) & niche leaves (a specific club): top ~5 each, per LEAF.
# Uncategorised (major hard news #113 left homeless) gets a Tier-2 depth so a big
# disaster/crime can still be promoted to the front page. ≈350–400 stories total.
TIER_DEPTH = {1: 50, 2: 20, 3: 5}
UNCAT_DEPTH = 20
# Floor guarantee: every distinct leaf contributes at least its top-2, so a story that can
# reach a bulletin via a selected category's guaranteed floor is never skipped, even if it
# falls below its tier's depth.
FLOOR_N = 2
# Above this many reviewed stories, split into tier BANDS (front page first) so each Haiku
# call attends carefully rather than skimming one huge prompt.
CALL_BATCH = 200
# FRONT-PAGE pass: the global top-N by score reviewed TOGETHER in one focused, score-ordered
# call. The significance / top_story judgment needs the biggest stories side by side — a large
# category-grouped batch (tier-laddered) buries them and flags nothing. This pass is
# authoritative for the front page; it overrides the tiered result for these high-rank stories.
FRONT_PAGE_N = 70

_DDL = """
CREATE TABLE IF NOT EXISTS data.editorial_reviews (
    id               BIGSERIAL PRIMARY KEY,
    ranking_run_id   BIGINT NOT NULL,
    story_id         TEXT NOT NULL,
    category         TEXT,
    significance     REAL NOT NULL DEFAULT 0,
    reason           TEXT,
    top_story        BOOLEAN NOT NULL DEFAULT false,
    model            TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ranking_run_id, story_id)
);
"""
# Additive migration for an existing table (the CREATE above is a no-op once it exists).
_MIGRATE = "ALTER TABLE data.editorial_reviews ADD COLUMN IF NOT EXISTS top_story BOOLEAN NOT NULL DEFAULT false;"
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
        self.db.execute(text(_MIGRATE))
        self.db.commit()
        _ensured = True

    def get(self, ranking_run_id: int) -> dict[str, EditorialAdjustment] | None:
        rows = self.db.execute(
            text("""SELECT story_id, category, significance, reason, top_story
                    FROM data.editorial_reviews WHERE ranking_run_id = :r"""),
            {"r": ranking_run_id},
        ).all()
        if not rows:
            return None  # not yet reviewed
        out: dict[str, EditorialAdjustment] = {}
        for sid, cat, sig, reason, top in rows:
            if sid == _MARKER:
                continue
            out[sid] = EditorialAdjustment(story_id=sid, category=cat,
                                           significance_delta=sig or 0.0, reason=reason or "",
                                           top_story=bool(top))
        return out

    def put(self, ranking_run_id: int, adjustments: dict[str, EditorialAdjustment], model: str) -> None:
        rows = [(_MARKER, None, 0.0, "", False)]  # always mark the run reviewed
        rows += [(a.story_id, a.category, a.significance_delta, a.reason, a.top_story)
                 for a in adjustments.values()]
        for sid, cat, sig, reason, top in rows:
            self.db.execute(
                text("""INSERT INTO data.editorial_reviews
                            (ranking_run_id, story_id, category, significance, reason, top_story, model)
                        VALUES (:r, :s, :c, :sig, :reason, :top, :m)
                        ON CONFLICT (ranking_run_id, story_id) DO NOTHING"""),
                {"r": ranking_run_id, "s": sid, "c": cat, "sig": sig, "reason": reason,
                 "top": top, "m": model},
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


def _group_key_cap_band(cat: str | None) -> tuple[str, int, str]:
    """(group key, per-group depth, tier band) for a category. Tier 1/2 group by top-level
    (broad, deep review); tier 3 groups by the exact leaf (niche, shallow). Band 'A' =
    front-page-critical (tier 1 + uncategorised, which can be promoted to top-stories);
    band 'B' = the rest — so, when split, the front page is reviewed most carefully."""
    if cat is None:
        return ("__uncat__", UNCAT_DEPTH, "A")
    tier = get_category_tier(cat)
    if tier == 1:
        return (cat.split(".")[0], TIER_DEPTH[1], "A")
    if tier == 2:
        return (cat.split(".")[0], TIER_DEPTH[2], "B")
    return (cat, TIER_DEPTH[3], "B")


def _tiered_review(scored: list[ScoredStory]) -> tuple[list[tuple[ScoredStory, str]], dict[str, int]]:
    """The tier-laddered review set: top-`depth` per category by tier, PLUS the top-`FLOOR_N`
    of every distinct leaf (floor guarantee). Returns [(story, band)] deduped and the global
    rank map (position by score across ALL candidates) for prompt context."""
    ordered = sorted(scored, key=lambda s: s.normalized_score, reverse=True)
    rank = {s.candidate.story_id: i + 1 for i, s in enumerate(ordered)}

    per_group: dict[str, list[ScoredStory]] = defaultdict(list)
    per_leaf: dict[str | None, list[ScoredStory]] = defaultdict(list)
    for s in ordered:  # already score-desc, so slices are top-N
        key, _, _ = _group_key_cap_band(s.candidate.primary_category)
        per_group[key].append(s)
        per_leaf[s.candidate.primary_category].append(s)

    picked: dict[str, str] = {}  # story_id -> band
    order: list[ScoredStory] = []

    def _take(s: ScoredStory) -> None:
        sid = s.candidate.story_id
        if sid not in picked:
            picked[sid] = _group_key_cap_band(s.candidate.primary_category)[2]
            order.append(s)

    # Tier-laddered depth per category (each group already score-desc → top-cap).
    for key, items in per_group.items():
        cap = _group_key_cap_band(items[0].candidate.primary_category)[1]
        for s in items[:cap]:
            _take(s)
    # Floor guarantee: every leaf's top-FLOOR_N, even below its tier depth.
    for items in per_leaf.values():
        for s in items[:FLOOR_N]:
            _take(s)

    return [(s, picked[s.candidate.story_id]) for s in order], rank


def _build_meta(db: Session, stories: list[ScoredStory], rank: dict[str, int]) -> list[StoryMeta]:
    snips = _snippets(db, [s.candidate.story_id for s in stories])
    metas = [
        StoryMeta(
            story_id=s.candidate.story_id,
            headline=s.candidate.title,
            snippet=snips.get(s.candidate.story_id, ""),
            category=s.candidate.primary_category,
            sources=s.candidate.source_count,
            rank=rank.get(s.candidate.story_id, 0),
        )
        for s in stories
    ]
    metas.sort(key=lambda m: m.rank)   # present in true ranked order
    return metas


def editorial_adjustments(
    db: Session, ranking_run_id: int, scored: list[ScoredStory],
) -> dict[str, EditorialAdjustment]:
    """The review for this ranking run (cached). Empty dict if disabled/unavailable.

    Reviews the tier-laddered set (≈350–400 stories). Small enough → one Haiku call; larger
    → split into tier bands (front page first) so each call attends carefully. Merged and
    cached once per ranking run."""
    if not settings.enable_editorial_pass or not settings.anthropic_api_key:
        return {}
    repo = EditorialRepo(db)
    repo.ensure_table()
    cached = repo.get(ranking_run_id)
    if cached is not None:
        return cached

    review, rank = _tiered_review(scored)
    leaves = sorted(load_valid_category_slugs())

    if len(review) <= CALL_BATCH:
        batches = [[s for s, _ in review]]
    else:
        # Band A (front-page-critical) reviewed first / together; then band B.
        band_a = [s for s, b in review if b == "A"]
        band_b = [s for s, b in review if b == "B"]
        batches = [band_a[i:i + CALL_BATCH] for i in range(0, len(band_a), CALL_BATCH)] or [[]]
        batches += [band_b[i:i + CALL_BATCH] for i in range(0, len(band_b), CALL_BATCH)]

    adjustments: dict[str, EditorialAdjustment] = {}
    # DEEP corrections across the tier-laddered set (category fixes beyond the front page).
    for batch in batches:
        if not batch:
            continue
        adjustments.update(review_front_page(_build_meta(db, batch, rank), leaves))

    # FRONT-PAGE pass — the global top-N by score, reviewed together, is the RELIABLE place for
    # the significance / top_story judgment. Runs LAST so its result overrides the (diluted)
    # tiered one for these high-rank stories.
    front = sorted(scored, key=lambda s: s.normalized_score, reverse=True)[:FRONT_PAGE_N]
    if front:
        adjustments.update(review_front_page(_build_meta(db, front, rank), leaves))

    repo.put(ranking_run_id, adjustments, _model())
    return adjustments


def apply_editorial(scored: list[ScoredStory], adjustments: dict[str, EditorialAdjustment]) -> int:
    """Mutate the scored candidates in place with the bounded adjustments. Returns the number
    of stories actually changed. `top_story` is set as a SEPARATE flag (the front page admits
    only flagged stories); `category` is a plain correction (no longer used to promote).
    A safety cap keeps at most MAX_PROMOTIONS top_story flags — the strongest by significance
    then score — so an over-eager review can't flood the front page."""
    if not adjustments:
        return 0
    by_id = {s.candidate.story_id: s for s in scored}

    tops = [sid for sid, a in adjustments.items() if sid in by_id and a.top_story]
    tops.sort(key=lambda sid: (adjustments[sid].significance_delta, by_id[sid].normalized_score),
              reverse=True)
    allowed_top = set(tops[:MAX_PROMOTIONS])

    changed = 0
    for sid, a in adjustments.items():
        s = by_id.get(sid)
        if s is None:
            continue
        touched = False
        # CATEGORY: a correction of what the story IS (never a front-page promotion now).
        if a.category and a.category != s.candidate.primary_category:
            s.candidate.primary_category = a.category
            touched = True
        if a.significance_delta:
            s.normalized_score = max(0.0, min(10.0, s.normalized_score + a.significance_delta))
            touched = True
        # TOP STORY: the separate significance flag the front-page block gates on.
        if a.top_story and sid in allowed_top and not s.candidate.top_story:
            s.candidate.top_story = True
            touched = True
        if touched:
            changed += 1
    return changed
