"""
Inspect and optionally apply LLM fallback categorisation decisions.

Shows every classify_fallback() result for currently-uncategorised articles,
with ACCEPTED / REJECTED clearly labelled.

Usage:
    # Preview only (default) — no DB writes
    cd backend && .venv/bin/python src/core/pipeline/devtools/fallback_inspect.py

    # Apply accepted decisions to the DB
    cd backend && .venv/bin/python src/core/pipeline/devtools/fallback_inspect.py --apply

Requires ANTHROPIC_API_KEY in environment or .env.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, "src")

# Load .env before importing pipeline modules
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv not installed; rely on environment variables being set
    pass

# Manually load .env if dotenv not available
if not os.environ.get("ANTHROPIC_API_KEY"):
    env_path = os.path.join(os.path.dirname(__file__), "../../../../..", ".env")
    env_path = os.path.normpath(env_path)
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    if key.strip() and not os.environ.get(key.strip()):
                        os.environ[key.strip()] = val.strip()

import logging
logging.basicConfig(
    level=logging.WARNING,  # suppress INFO from classifier; we handle display ourselves
    format="%(levelname)s %(name)s: %(message)s",
)

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/headlines")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from core.platform.db.session import SessionLocal
from core.pipeline.data.normalise.categorise.category_loader import load_valid_category_slugs
from core.pipeline.data.normalise.categorise.llm_fallback import (
    classify_fallback,
    FALLBACK_CONFIDENCE_THRESHOLD,
)
from sqlalchemy import text


def _bar(confidence: float) -> str:
    filled = round(confidence * 10)
    return "█" * filled + "░" * (10 - filled)


def main(apply: bool = False) -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set. Add it to .env or set in environment.")
        sys.exit(1)

    valid_slugs = list(load_valid_category_slugs())

    with SessionLocal() as db:
        rows = db.execute(text("""
            SELECT id, title, content_snippet, ingestion_run_id
            FROM data.normalisation_articles
            WHERE category_primary IS NULL
            ORDER BY id ASC
        """)).fetchall()

    print(f"\n{'─'*100}")
    print(f"  LLM FALLBACK INSPECTION — {len(rows)} uncategorised articles")
    model_name = os.environ.get("FALLBACK_MODEL", "claude-haiku-4-5-20251001")
    print(f"  Model: {model_name}  Threshold: {FALLBACK_CONFIDENCE_THRESHOLD}  Mode: {'APPLY' if apply else 'PREVIEW'}")
    print(f"{'─'*100}\n")

    accepted: list[tuple] = []
    rejected: list[tuple] = []
    errors: list[tuple] = []

    for i, row in enumerate(rows, 1):
        art_id, title, snippet, run_id = row
        print(f"  [{i:2d}/{len(rows)}] id={art_id}  run={run_id}")
        print(f"        {title[:90]}")

        result = classify_fallback(title, snippet, valid_slugs, article_id=art_id)

        if result is None:
            print(f"        ⚠ API call failed or key not set\n")
            errors.append((art_id, title))
            continue

        bar = _bar(result.confidence)
        verdict = "ACCEPTED" if result.accepted else "REJECTED"
        symbol = "✓" if result.accepted else "✗"

        print(
            f"        {symbol} {verdict:8s}  [{bar}] {result.confidence:.2f}  "
            f"slug={result.slug}  reason={result.reason!r}\n"
        )

        if result.accepted:
            accepted.append((art_id, title, result.slug, result.confidence, result.reason))
        else:
            rejected.append((art_id, title, result.slug, result.confidence, result.reason))

        # Brief pause between API calls to stay within rate limits
        if i < len(rows):
            time.sleep(0.15)

    # Summary
    print(f"\n{'─'*100}")
    print(f"  SUMMARY  accepted={len(accepted)}  rejected={len(rejected)}  errors={len(errors)}")
    print(f"{'─'*100}\n")

    if accepted:
        print("  ACCEPTED decisions:")
        for art_id, title, slug, conf, reason in accepted:
            print(f"    [{art_id:3d}] {conf:.2f}  {slug:40s}  {title[:55]}")

    if rejected:
        print("\n  REJECTED decisions (confidence below threshold):")
        for art_id, title, slug, conf, reason in rejected:
            print(f"    [{art_id:3d}] {conf:.2f}  {slug:40s}  {title[:55]}")

    if errors:
        print(f"\n  ERRORS ({len(errors)} articles — API failures):")
        for art_id, title in errors:
            print(f"    [{art_id:3d}] {title[:70]}")

    if not apply:
        if accepted:
            print(f"\n  Run with --apply to write {len(accepted)} accepted classification(s) to the DB.")
        return

    # Apply accepted decisions
    if not accepted:
        print("\n  Nothing to apply.")
        return

    with SessionLocal() as db:
        for art_id, title, slug, conf, reason in accepted:
            db.execute(text("""
                UPDATE data.normalisation_articles
                SET
                    category_primary    = :slug,
                    category_slugs      = ARRAY[:slug]::text[],
                    category_method     = 'llm-fallback',
                    category_version    = 1
                WHERE id = :id AND category_primary IS NULL
            """), {"slug": slug, "id": art_id})
        db.commit()

    print(f"\n  Applied {len(accepted)} fallback classification(s) to data.normalisation_articles.")


if __name__ == "__main__":
    apply_flag = "--apply" in sys.argv
    main(apply=apply_flag)
