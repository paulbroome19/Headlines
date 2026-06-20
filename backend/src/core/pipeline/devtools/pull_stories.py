#!/usr/bin/env python3
"""Pull stories through the free pipeline stages: ingest → normalise → cluster → categorise → rank.

Fetches top-headlines from 5 GNews topics (up to 10 each), deduplicates,
and runs everything in-process — no dispatcher or consumer required.

Paid API calls: GNews only (plus optional Haiku categorisation fallback if
ENABLE_LLM_CATEGORISE_FALLBACK=true, which is the default).
Zero LLM summarisation. Zero TTS. Safe to run on a schedule.

Summarisation and TTS happen on-demand when Generate is pressed in the app.

Run directly via the venv:
    cd backend && .venv/bin/python3.11 src/core/pipeline/devtools/pull_stories.py
"""

from __future__ import annotations

import hashlib
import os
import sys
import uuid
from datetime import datetime, timezone

# Change to backend/ before any core import so Settings() finds .env
_BACKEND_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../../")
)
os.chdir(_BACKEND_DIR)

from sqlalchemy import text  # noqa: E402

from core.platform.db.session import SessionLocal  # noqa: E402
from core.platform.providers.gnews import GNewsClient, GNewsArticle  # noqa: E402
from core.pipeline.data.ingest.repos.ingestion_run_repo import IngestionRunRepo  # noqa: E402
from core.pipeline.data.ingest.repos.ingested_article_repo import IngestedArticleRepo  # noqa: E402
from core.pipeline.data.normalise.handlers.requested import handle_normalise_requested  # noqa: E402
from core.pipeline.data.cluster.handlers.requested import handle_cluster_requested  # noqa: E402
from core.pipeline.data.categorise.handlers.cluster_completed import handle_cluster_completed  # noqa: E402
from core.pipeline.ranking.handlers.categorise_completed import handle_categorise_completed  # noqa: E402

# (label, GNews topic id)  —  None = general top-headlines
TOPICS = [
    ("general",    None),
    ("world",      "WORLD"),
    ("nation",     "NATION"),
    ("technology", "TECHNOLOGY"),
    ("sports",     "SPORTS"),
]


def _dedup_hash(url: str) -> str:
    return hashlib.sha256(url.strip().lower().encode()).hexdigest()


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _step(n: int, label: str) -> None:
    print(f"\n[{n}/5] {label}")


def _ok(msg: str) -> None:
    print(f"      ✓ {msg}")


def _warn(msg: str) -> None:
    print(f"      ! {msg}")


def _fail(msg: str) -> None:
    print(f"      ✗ {msg}", file=sys.stderr)


def main() -> None:
    now = datetime.now(timezone.utc)
    print(f"\nPull Stories — {now.strftime('%Y-%m-%d %H:%M UTC')}")

    # ── 1: Ingest (multi-topic) ───────────────────────────────────────────────
    _step(1, "Fetching articles from GNews (5 topics)...")

    client = GNewsClient()
    fetched_by_hash: dict[str, GNewsArticle] = {}
    topic_counts: dict[str, int] = {}

    for label, topic_id in TOPICS:
        try:
            articles = client.top_headlines(lang="en", country="gb", max_results=10, topic=topic_id)
        except Exception as e:
            _warn(f"{label}: fetch failed — {e}")
            topic_counts[label] = 0
            continue
        topic_counts[label] = len(articles)
        for a in articles:
            if a.url:
                fetched_by_hash.setdefault(_dedup_hash(a.url), a)

    total_fetched = sum(topic_counts.values())
    unique_fetched = len(fetched_by_hash)
    print(f"      GNews returned {total_fetched} total, {unique_fetched} unique after cross-topic dedup:")
    for label, count in topic_counts.items():
        print(f"        {label:<12} {count}")

    if not fetched_by_hash:
        _fail("No articles fetched — check GNEWS_API_KEY and network access.")
        sys.exit(1)

    with SessionLocal() as db:
        run = IngestionRunRepo(db).create(source="pull-stories")
        repo = IngestedArticleRepo(db)
        for dedup_hash, a in fetched_by_hash.items():
            if not (a.title and a.url):
                continue
            repo.create(
                ingestion_run_id=run.id,
                provider="gnews",
                provider_item_id=None,
                url=a.url,
                dedup_hash=dedup_hash,
                raw=a.raw,
                title=a.title,
                publisher=a.source_name,
                published_at=_parse_date(a.published_at),
            )
        db.commit()
        run_id = run.id

    with SessionLocal() as db:
        new_count = int(db.execute(
            text("SELECT COUNT(*) FROM data.ingested_articles WHERE ingestion_run_id = :id"),
            {"id": run_id},
        ).scalar_one())

    already_in_db = unique_fetched - new_count
    _ok(f"run #{run_id}: {new_count} new  /  {already_in_db} already in DB  (of {unique_fetched} unique)")

    if new_count == 0:
        _warn("All fetched articles already in DB — nothing new to normalise or cluster.")
        _warn("Ranking will still refresh using existing 48h stories.")

    # ── 2: Normalise ──────────────────────────────────────────────────────────
    _step(2, "Normalising articles...")
    handle_normalise_requested({
        "type": "data.normalise.requested",
        "payload": {"ingestion_run_id": run_id},
        "trace_id": None,
    })

    with SessionLocal() as db:
        norm_count = int(db.execute(
            text("SELECT COUNT(*) FROM data.normalisation_articles WHERE ingestion_run_id = :id"),
            {"id": run_id},
        ).scalar_one())

    _ok(f"{norm_count} articles normalised")

    # ── 3: Cluster ────────────────────────────────────────────────────────────
    _step(3, "Clustering into stories...")
    handle_cluster_requested({
        "type": "data.cluster.requested",
        "payload": {"ingestion_run_id": run_id},
        "trace_id": None,
    })

    with SessionLocal() as db:
        story_ids = list(db.execute(
            text("""
                SELECT DISTINCT story_id
                FROM data.normalisation_articles
                WHERE ingestion_run_id = :id AND story_id IS NOT NULL
            """),
            {"id": run_id},
        ).scalars().all())

    if story_ids:
        _ok(f"{len(story_ids)} stories from this run")
    else:
        _warn("No new stories from this run — using recent DB stories for ranking")
        with SessionLocal() as db:
            story_ids = list(db.execute(text("""
                SELECT id FROM data.stories
                WHERE last_published_at > NOW() - INTERVAL '48 hours'
                ORDER BY id
            """)).scalars().all())
        if not story_ids:
            _fail("No stories in DB within 48h — run again after headlines have rotated.")
            sys.exit(1)
        _ok(f"{len(story_ids)} recent stories found in DB (48h window)")

    # ── 4: Categorise ─────────────────────────────────────────────────────────
    _step(4, "Categorising stories...")
    handle_cluster_completed({
        "type": "data.cluster.completed",
        "payload": {
            "story_ids": story_ids,
            "cluster_batch_id": str(run_id),
            "ingestion_run_id": run_id,
        },
        "trace_id": None,
    })
    _ok("categories resolved by majority vote")

    # ── 5: Rank ───────────────────────────────────────────────────────────────
    _step(5, "Ranking stories (global 48h window)...")
    categorisation_batch_id = str(uuid.uuid4())
    handle_categorise_completed({
        "type": "data.categorise.completed",
        "payload": {
            "categorisation_batch_id": categorisation_batch_id,
            "story_ids": story_ids,
            "cluster_batch_id": str(run_id),
            "ingestion_run_id": run_id,
        },
        "trace_id": None,
    })

    with SessionLocal() as db:
        ranking_run = db.execute(
            text("SELECT id, candidate_count, top_stories, briefing FROM data.ranking_runs ORDER BY id DESC LIMIT 1")
        ).mappings().first()

    if not ranking_run:
        _fail("Ranking run not created.")
        sys.exit(1)
    _ok(f"ranking run #{ranking_run['id']} ({ranking_run['candidate_count']} candidates)")

    with SessionLocal() as db:
        cat_breakdown = db.execute(text("""
            SELECT primary_category, COUNT(*) AS cnt
            FROM data.stories
            WHERE id = ANY(:ids)
            GROUP BY primary_category
            ORDER BY cnt DESC
        """), {"ids": story_ids}).mappings().all()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n── Category breakdown (this run's stories) " + "─" * 17)
    for row in cat_breakdown:
        cat = row["primary_category"] or "(uncategorised)"
        print(f"  {cat:<40} {int(row['cnt']):>3}")

    print("\n── Summary " + "─" * 49)
    print(f"  GNews fetched       : {total_fetched} total ({unique_fetched} unique)")
    print(f"  New to DB           : {new_count}")
    print(f"  Already in DB       : {already_in_db}")
    print(f"  Articles normalised : {norm_count}")
    print(f"  Stories (this run)  : {len(story_ids)}")
    print(f"  Ranking run         : #{ranking_run['id']}  ({ranking_run['candidate_count']} candidates)")
    print(f"  Summarisation       : deferred — happens on-demand when Generate is pressed")
    print(f"  Paid API calls      : GNews only (LLM categorise fallback if needed)")
    print()


if __name__ == "__main__":
    main()
