"""
One-time backfill: re-categorise stories through the LLM-primary path so legacy /
invalid primaries (world.*, entertainment.*, climate, politics.global, sport.formula1,
the singular/plural slug drift, etc.) map to current taxonomy leaves — or are DROPPED
when the story has no honest home in our taxonomy (correctness over coverage).

DRY-RUN by default — reads + classifies + prints, writes NOTHING (no cache table, no
story updates). Pass --commit to persist (updates data.stories; drops set NULL; caches).

Outcomes per story:
  KEEP        primary unchanged and still valid
  RECLASSIFY  primary changed to a different valid target
  DROP        no honest taxonomy fit → excluded (primary → NULL on commit)
  NO-LLM      API unavailable/failed → left as-is

Usage (from backend/):
  .venv/bin/python -m core.pipeline.devtools.recategorise_stories                 # dry-run, invalid-only
  .venv/bin/python -m core.pipeline.devtools.recategorise_stories --all --limit 40 # dry-run, any stories
  .venv/bin/python -m core.pipeline.devtools.recategorise_stories --commit          # persist invalid-only
"""
from __future__ import annotations

import argparse
from collections import Counter

from sqlalchemy import text

from core.platform.db.session import SessionLocal
from core.pipeline.data.normalise.categorise.category_loader import load_valid_category_slugs
from core.pipeline.data.normalise.categorise.cluster_categoriser import (
    _load_story_inputs, categorise_story,
)
from core.pipeline.data.normalise.categorise.llm_categoriser import (
    DROP, _ancestor_nodes, classify_story,
)


def _valid_nodes() -> set[str]:
    return _ancestor_nodes(list(load_valid_category_slugs()))


def _targets(db, *, all_stories: bool, limit: int | None) -> list[tuple[int, str | None]]:
    # "before" = the REPRESENTATIVE article's category (what ranking actually reads), not
    # data.stories.primary_category. Representative = latest-published member (matches
    # candidate_loader's DISTINCT ON published_at DESC).
    rows = db.execute(text("""
        SELECT s.id, rep.category_primary
        FROM data.stories s
        LEFT JOIN LATERAL (
            SELECT na.category_primary
            FROM data.normalisation_articles na
            WHERE na.story_id = s.id
            ORDER BY na.published_at DESC NULLS LAST, na.id DESC
            LIMIT 1
        ) rep ON true
        ORDER BY s.id
    """)).all()
    nodes = _valid_nodes()
    out = [(sid, pc) for sid, pc in rows if all_stories or (not pc) or (pc not in nodes)]
    return out[:limit] if limit is not None else out


def _persist(db, story_id: int, category: str | None) -> None:
    """Write to the field ranking reads (all articles' category_primary) + the story row."""
    db.execute(text("UPDATE data.normalisation_articles SET category_primary = :c, "
                    "category_method = :m WHERE story_id = :id"),
               {"c": category, "m": "llm-drop" if category is None else "llm-primary", "id": story_id})
    db.execute(text("UPDATE data.stories SET primary_category = :c WHERE id = :id"),
               {"c": category, "id": story_id})


def _classify_readonly(db, story_id: int):
    """Dry-run classification: read inputs + call the LLM, but write NOTHING (no cache)."""
    leaves = sorted(load_valid_category_slugs())
    inp = _load_story_inputs(db, story_id)
    if inp is None:
        return None
    return classify_story(
        title=inp["title"], snippets=inp["snippets"],
        pool_topic=inp["pool_topic"], pool_country=inp["pool_country"],
        valid_leaves=leaves,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="any story, not just invalid ones")
    ap.add_argument("--commit", action="store_true", help="persist (default: dry-run)")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    with SessionLocal() as db:
        targets = _targets(db, all_stories=args.all, limit=args.limit)
        mode = "COMMIT" if args.commit else "DRY-RUN"
        print(f"targets: {len(targets)} stories  ({'ALL' if args.all else 'invalid-only'}, {mode})\n")

        tally: Counter[str] = Counter()
        for sid, before in targets:
            result = categorise_story(db, sid) if args.commit else _classify_readonly(db, sid)

            if result is None:
                tally["NO-LLM"] += 1
                print(f"  NO-LLM     {sid}: kept {before!r}")
                continue
            if result.primary == DROP:
                tally["DROP"] += 1
                print(f"  DROP       {sid}: {before!r:<32} -> (excluded)   [{result.reason}]")
                if args.commit:
                    _persist(db, sid, None)
                continue

            after = result.primary
            kind = "KEEP      " if after == before else "RECLASSIFY"
            tally["KEEP" if after == before else "RECLASSIFY"] += 1
            print(f"  {kind} {sid}: {before!r:<32} -> {after!r}   [{result.reason}]")
            if args.commit and after != before:
                _persist(db, sid, after)

        if args.commit:
            db.commit()
        print(f"\n{mode}: " + "  ".join(f"{k}={v}" for k, v in sorted(tally.items()))
              + ("" if args.commit else "   (re-run with --commit to persist)"))


if __name__ == "__main__":
    main()
