"""
Regenerate story summaries for a ranking run using the current summariser prompt.

Bypasses the content_hash cache — every targeted story gets a fresh LLM call.
Existing rows are updated in-place (same story_id + ranking_run_id); summary_version
is incremented so you can see which generation a row came from.

Usage:
    # Dry run — shows what would regenerate, no LLM calls
    cd backend && .venv/bin/python src/core/pipeline/devtools/regenerate_summaries.py \\
        --ranking-run-id 16

    # Regenerate one story (still dry run until --confirm)
    .venv/bin/python src/core/pipeline/devtools/regenerate_summaries.py \\
        --ranking-run-id 16 --story-id 82

    # Regenerate all stories for a run
    .venv/bin/python src/core/pipeline/devtools/regenerate_summaries.py \\
        --ranking-run-id 16 --confirm

    # Regenerate one story (confirm required even for single story)
    .venv/bin/python src/core/pipeline/devtools/regenerate_summaries.py \\
        --ranking-run-id 16 --story-id 82 --confirm

    # Override model just for this run
    .venv/bin/python src/core/pipeline/devtools/regenerate_summaries.py \\
        --ranking-run-id 16 --confirm --model claude-haiku-4-5-20251001

Requires ANTHROPIC_API_KEY in environment or .env.
Does NOT touch TTS, bulletins, or audio.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, "src")

# ── env bootstrap ──────────────────────────────────────────────────────────────

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Manual fallback: 4 levels up from devtools/ = backend/ (where .env lives)
_env_path = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "../../../..", ".env")
)
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                if _k.strip() and not os.environ.get(_k.strip()):
                    os.environ[_k.strip()] = _v.strip()

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/headlines",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

# ── imports ────────────────────────────────────────────────────────────────────

import hashlib

from sqlalchemy import text

from core.platform.db.session import SessionLocal
from core.pipeline.data.summarise.llm_summariser import summarise_story, _model


# ── helpers ────────────────────────────────────────────────────────────────────

def _wc(s: str) -> int:
    return len((s or "").split())


def _trunc(s: str, n: int) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s[:n] + "…" if len(s) > n else s


def _compute_content_hash(articles: list[dict]) -> str:
    parts = []
    for a in articles[:5]:
        title = (a.get("title") or "").strip()
        snippet = (a.get("snippet") or "")[:300].strip()
        parts.append(f"{title}|{snippet}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def _divider(label: str = "", width: int = 80) -> str:
    if label:
        pad = width - len(label) - 4
        return f"── {label} {'─' * max(pad, 2)}"
    return "─" * width


def _print_story_fields(label: str, version: int, conf: float, row: dict) -> None:
    print(f"  {label} (v{version}, conf={conf:.2f}):")
    print(f"    headline:       {_trunc(row.get('headline', ''), 80)!r}")
    print(f"    summary_text:   {_trunc(row.get('summary_text', ''), 80)!r}  [{_wc(row.get('summary_text', ''))}w]")
    print(f"    why_it_matters: {_trunc(row.get('why_it_matters', ''), 80)!r}")
    print(f"    audio_script:   {_trunc(row.get('audio_script', ''), 80)!r}  [{_wc(row.get('audio_script', ''))}w]")


# ── data loading ───────────────────────────────────────────────────────────────

def _load_run_stories(db, ranking_run_id: int) -> list[str]:
    """Return story_ids for a ranking run in ranking order (top first, then briefing)."""
    row = db.execute(text("""
        SELECT top_stories, briefing
        FROM data.ranking_runs
        WHERE id = :run_id
    """), {"run_id": ranking_run_id}).fetchone()
    if not row:
        return []
    top = [str(s["story_id"]) for s in (row[0] or [])]
    briefing = [str(s["story_id"]) for s in (row[1] or [])]
    seen: set[str] = set()
    ordered: list[str] = []
    for sid in top + briefing:
        if sid not in seen:
            seen.add(sid)
            ordered.append(sid)
    return ordered


def _load_existing_summaries(db, ranking_run_id: int) -> dict[str, dict]:
    rows = db.execute(text("""
        SELECT story_id::text, headline, summary_text, why_it_matters,
               audio_script, confidence, summary_version, model, content_hash
        FROM data.story_summaries
        WHERE ranking_run_id = :run_id
    """), {"run_id": ranking_run_id}).mappings().all()
    return {r["story_id"]: dict(r) for r in rows}


def _load_articles(db, story_id: str) -> list[dict]:
    rows = db.execute(text("""
        SELECT title, content_snippet
        FROM data.normalisation_articles
        WHERE story_id::text = :story_id
        ORDER BY published_at DESC NULLS LAST
        LIMIT 5
    """), {"story_id": story_id}).fetchall()
    return [{"title": r[0], "snippet": r[1]} for r in rows]


def _load_category(db, story_id: str) -> str | None:
    row = db.execute(text("""
        SELECT primary_category
        FROM data.stories
        WHERE id::text = :story_id
    """), {"story_id": story_id}).fetchone()
    return row[0] if row else None


# ── upsert ─────────────────────────────────────────────────────────────────────

def _upsert_summary(db, *, story_id: str, ranking_run_id: int, content_hash: str,
                    headline: str, summary_text: str, why_it_matters: str | None,
                    audio_script: str | None, model: str, confidence: float) -> int:
    """
    Upsert summary row. Returns new summary_version.
    ON CONFLICT: updates all fields and increments summary_version.
    """
    row = db.execute(text("""
        INSERT INTO data.story_summaries (
            story_id, ranking_run_id, content_hash,
            headline, summary_text, why_it_matters, audio_script,
            model, summary_version, confidence, created_at
        ) VALUES (
            :story_id, :ranking_run_id, :content_hash,
            :headline, :summary_text, :why_it_matters, :audio_script,
            :model, 1, :confidence, now()
        )
        ON CONFLICT (story_id, ranking_run_id) DO UPDATE SET
            headline        = EXCLUDED.headline,
            summary_text    = EXCLUDED.summary_text,
            why_it_matters  = EXCLUDED.why_it_matters,
            audio_script    = EXCLUDED.audio_script,
            model           = EXCLUDED.model,
            content_hash    = EXCLUDED.content_hash,
            confidence      = EXCLUDED.confidence,
            summary_version = data.story_summaries.summary_version + 1,
            created_at      = now()
        RETURNING summary_version
    """), {
        "story_id": story_id,
        "ranking_run_id": ranking_run_id,
        "content_hash": content_hash,
        "headline": headline,
        "summary_text": summary_text,
        "why_it_matters": why_it_matters or None,
        "audio_script": audio_script or None,
        "model": model,
        "confidence": confidence,
    }).fetchone()
    return row[0]


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate story summaries bypassing content-hash cache.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ranking-run-id", type=int, required=True,
                        help="Ranking run to regenerate summaries for")
    parser.add_argument("--story-id", type=str, default=None,
                        help="Regenerate one specific story only")
    parser.add_argument("--confirm", action="store_true",
                        help="Actually run LLM calls and write to DB (default: dry run)")
    parser.add_argument("--model", type=str, default=None,
                        help="Override SUMMARISE_MODEL for this run")
    args = parser.parse_args()

    if args.model:
        os.environ["SUMMARISE_MODEL"] = args.model

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set. Add it to .env or set in environment.")
        sys.exit(1)

    model = _model()
    run_id = args.ranking_run_id
    target_story_id = args.story_id

    # ── Load data ──────────────────────────────────────────────────────────────

    with SessionLocal() as db:
        # Verify run exists
        exists = db.execute(text(
            "SELECT 1 FROM data.ranking_runs WHERE id = :id"
        ), {"id": run_id}).fetchone()
        if not exists:
            print(f"ERROR: ranking_run_id={run_id} not found in data.ranking_runs.")
            sys.exit(1)

        ordered_ids = _load_run_stories(db, run_id)
        existing = _load_existing_summaries(db, run_id)

        # Determine target list
        if target_story_id:
            if target_story_id not in ordered_ids:
                print(f"ERROR: story_id={target_story_id!r} not in ranking_run_id={run_id}.")
                print(f"  Run contains: {', '.join(ordered_ids)}")
                sys.exit(1)
            targets = [target_story_id]
        else:
            targets = ordered_ids

        if not targets:
            print(f"No stories found for ranking_run_id={run_id}.")
            sys.exit(0)

        # Load articles + categories for all targets
        story_data: dict[str, dict] = {}
        for sid in targets:
            articles = _load_articles(db, sid)
            category = _load_category(db, sid)
            story_data[sid] = {
                "articles": articles,
                "category": category,
                "title": articles[0].get("title", "") if articles else "",
            }

    # ── Header ─────────────────────────────────────────────────────────────────

    mode_label = "DRY RUN" if not args.confirm else "CONFIRM"
    scope_label = f"story={target_story_id}" if target_story_id else f"all {len(targets)} stories"

    print()
    print(_divider())
    print(f"  SUMMARY REGENERATION  run={run_id}  {scope_label}  [{mode_label}]")
    print(f"  Model: {model}  |  Cache bypass: yes  |  TTS: never")
    print(_divider())
    print()

    # ── Story inventory ────────────────────────────────────────────────────────

    skipped: list[str] = []
    workable: list[str] = []

    for sid in targets:
        articles = story_data[sid]["articles"]
        if not articles:
            skipped.append(sid)
        else:
            workable.append(sid)

    if skipped:
        print(f"  SKIPPED (no articles): {', '.join(skipped)}\n")

    for i, sid in enumerate(workable, 1):
        prev = existing.get(sid)
        cat = story_data[sid]["category"] or "—"
        title = _trunc(story_data[sid]["title"], 65)
        if prev:
            status = f"v{prev['summary_version']}  conf={prev['confidence']:.2f}"
        else:
            status = "no existing summary"
        print(f"  [{i:2d}/{len(workable)}]  story={sid:<6}  cat={cat:<35}  {status}")
        print(f"          {title!r}")

    print()
    print(f"  Estimated LLM calls: {len(workable)}")

    if not args.confirm:
        print()
        print("  DRY RUN — no changes made.")
        if workable:
            scope = f"--story-id {workable[0]}" if len(workable) == 1 else ""
            print(f"  Add --confirm{' ' + scope if scope else ''} to regenerate.")
        print()
        return

    # ── LLM regeneration ──────────────────────────────────────────────────────

    print()
    counts = {"ok": 0, "failed": 0}

    for i, sid in enumerate(workable, 1):
        data = story_data[sid]
        articles = data["articles"]
        category = data["category"]
        title = data["title"]
        prev = existing.get(sid)

        print(_divider(f"[{i}/{len(workable)}] story={sid}  cat={category or '—'}", width=80))
        print()

        if prev:
            _print_story_fields("BEFORE", prev["summary_version"], prev["confidence"], prev)
        else:
            print("  BEFORE: (no existing summary)")
        print()

        result = summarise_story(
            story_id=sid,
            representative_title=title,
            articles=articles,
            category=category,
        )

        if result is None:
            print(f"  ✗ FAILED — LLM call returned None")
            counts["failed"] += 1
            print()
            continue

        content_hash = _compute_content_hash(articles)
        new_row = {
            "headline": result.headline,
            "summary_text": result.summary_text,
            "why_it_matters": result.why_it_matters,
            "audio_script": result.audio_script,
        }
        new_version_expected = (prev["summary_version"] + 1) if prev else 1

        _print_story_fields("AFTER ", new_version_expected, result.confidence, new_row)
        print()

        # Persist
        with SessionLocal() as db:
            new_version = _upsert_summary(
                db,
                story_id=sid,
                ranking_run_id=run_id,
                content_hash=content_hash,
                headline=result.headline,
                summary_text=result.summary_text,
                why_it_matters=result.why_it_matters or None,
                audio_script=result.audio_script or None,
                model=model,
                confidence=result.confidence,
            )
            db.commit()

        old_v = prev["summary_version"] if prev else 0
        print(f"  ✓ Updated  summary_version {old_v} → {new_version}")
        counts["ok"] += 1
        print()

        if i < len(workable):
            time.sleep(0.1)

    # ── Final summary ──────────────────────────────────────────────────────────

    print(_divider())
    print(
        f"  DONE  run={run_id}  updated={counts['ok']}  failed={counts['failed']}"
        + (f"  skipped={len(skipped)}" if skipped else "")
    )
    print(_divider())
    print()

    if counts["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
