"""
One-time backfill: re-categorise stories through the LLM-primary path so legacy /
invalid primaries (world.*, entertainment.*, climate, politics.global, sport.formula1,
the singular/plural slug drift, etc.) map to current taxonomy leaves.

DRY-RUN by default — prints before/after and writes NOTHING. Pass --commit to persist.

Usage (from backend/):
  .venv/bin/python -m core.pipeline.devtools.recategorise_stories                # dry-run, invalid-only
  .venv/bin/python -m core.pipeline.devtools.recategorise_stories --all          # dry-run, every story
  .venv/bin/python -m core.pipeline.devtools.recategorise_stories --commit        # persist invalid-only
  .venv/bin/python -m core.pipeline.devtools.recategorise_stories --limit 50      # cap

The DB URL comes from the standard app settings / env (same as the pipeline).
"""
from __future__ import annotations

import argparse

from sqlalchemy import text

from core.platform.db.session import SessionLocal
from core.pipeline.data.normalise.categorise.category_loader import load_valid_category_slugs
from core.pipeline.data.normalise.categorise.cluster_categoriser import categorise_story
from core.pipeline.data.normalise.categorise.llm_categoriser import _ancestor_nodes


def _valid_nodes() -> set[str]:
    return _ancestor_nodes(list(load_valid_category_slugs()))


def _target_story_ids(db, *, all_stories: bool, limit: int | None) -> list[tuple[int, str | None]]:
    rows = db.execute(text("""
        SELECT id, primary_category FROM data.stories ORDER BY id
    """)).all()
    nodes = _valid_nodes()
    out: list[tuple[int, str | None]] = []
    for sid, primary in rows:
        invalid = (not primary) or (primary not in nodes)
        if all_stories or invalid:
            out.append((sid, primary))
    if limit is not None:
        out = out[:limit]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="every story, not just invalid ones")
    ap.add_argument("--commit", action="store_true", help="persist (default: dry-run)")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    with SessionLocal() as db:
        targets = _target_story_ids(db, all_stories=args.all, limit=args.limit)
        print(f"targets: {len(targets)} stories  ({'ALL' if args.all else 'invalid-only'}, "
              f"{'COMMIT' if args.commit else 'DRY-RUN'})\n")

        changed = 0
        for sid, before in targets:
            result = categorise_story(db, sid)   # cached; guaranteed-valid or None
            if result is None:
                print(f"  {sid}: [no LLM result — kept {before!r}]")
                continue
            after = result.primary
            mark = "*" if after != before else " "
            if after != before:
                changed += 1
            print(f" {mark}{sid}: {before!r:<34} -> {after!r}  ({result.reason})")
            if args.commit and after != before:
                db.execute(
                    text("UPDATE data.stories SET primary_category = :c WHERE id = :id"),
                    {"c": after, "id": sid},
                )
        if args.commit:
            db.commit()
        print(f"\n{'COMMITTED' if args.commit else 'DRY-RUN'}: {changed}/{len(targets)} would change"
              + ("" if args.commit else "  (re-run with --commit to persist)"))


if __name__ == "__main__":
    main()
