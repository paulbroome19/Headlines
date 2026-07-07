from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import text

from core.platform.db.session import SessionLocal

from core.pipeline.data.summarise.llm_summariser import summarise_story, _model
from core.pipeline.data.summarise.repos.story_summary_repo import StorySummaryRepo

logger = logging.getLogger(__name__)

# Maximum concurrent LLM calls in ensure_story_summaries().
# Keeps Anthropic rate-limit headroom while masking per-call latency.
_MAX_PARALLEL_SUMMARIES = 6


def ensure_story_summaries(
    ranking_run_id: int,
    story_ids: list[str],
    depths: dict[str, tuple[str, int]] | None = None,
) -> dict[str, int]:
    """
    Ensure summaries exist in data.story_summaries for every story_id in story_ids
    under ranking_run_id. Called on-demand at manifest/bulletin-assembly time.

    depths: optional {story_id: (depth_tier, depth_words)} — depth-by-rank. A story
            not in the map (or depths=None) defaults to the 'lead' full treatment
            (back-compat with the eager pre-warm). Depth is part of the cache key,
            so the same story can hold up to 4 tiers.

    Cache semantics
    ---------------
    - Stories already summarised for this run AT THEIR REQUESTED DEPTH are skipped.
    - A matching (story_id, content_hash, model, depth_tier) row from ANY previous
      run is reused — no LLM call, just a new row inserted for this run.
    - Only true cache misses call the LLM, and those calls run in parallel.

    Returns counts: {attempted, generated, reused, failed}.
    """
    counts: dict[str, int] = {"attempted": 0, "generated": 0, "reused": 0, "failed": 0}

    if not story_ids:
        return counts

    depths = depths or {}

    def _depth(sid: str) -> tuple[str, int | None]:
        return depths.get(sid, ("lead", None))

    model = _model()

    # ── Phase 1: DB reads ────────────────────────────────────────────────────
    # Determine what needs work, load articles, check content cache.
    # Session is closed before any LLM call.

    stories_to_generate: list[str] = []
    stories_to_reuse: list[dict[str, Any]] = []
    story_articles: dict[str, list[dict]] = {}
    story_hashes: dict[str, str] = {}
    story_categories: dict[str, str | None] = {}

    with SessionLocal() as db:
        repo = StorySummaryRepo(db)
        # Depth-aware skip: a story is "done" for this run only at the depth already
        # stored — a story summarised at 'lead' still needs work if 'brief' is asked.
        done_rows = db.execute(text("""
            SELECT story_id, depth_tier FROM data.story_summaries WHERE ranking_run_id = :r
        """), {"r": ranking_run_id}).fetchall()
        done_pairs = {(str(row[0]), row[1]) for row in done_rows}
        pending = [sid for sid in story_ids if (str(sid), _depth(sid)[0]) not in done_pairs]

        if not pending:
            logger.info(
                "ensure_story_summaries run=%s: all %d stories already summarised at their depth",
                ranking_run_id, len(story_ids),
            )
            return counts

        logger.info(
            "ensure_story_summaries run=%s: %d total  %d done@depth  %d to process",
            ranking_run_id, len(story_ids), len(done_pairs), len(pending),
        )

        cat_rows = db.execute(text("""
            SELECT id::text, primary_category
            FROM data.stories
            WHERE id::text = ANY(:ids)
        """), {"ids": list(pending)}).fetchall()
        story_categories = {row[0]: row[1] for row in cat_rows}

        for story_id in pending:
            rows = db.execute(text("""
                SELECT title, content_snippet
                FROM data.normalisation_articles
                WHERE story_id::text = :story_id
                ORDER BY published_at DESC NULLS LAST
                LIMIT 5
            """), {"story_id": story_id}).fetchall()

            articles = [{"title": r[0], "snippet": r[1]} for r in rows]
            if not articles:
                logger.warning(
                    "ensure_story_summaries run=%s: no articles for story=%s — skipping",
                    ranking_run_id, story_id,
                )
                counts["failed"] += 1
                continue

            story_articles[story_id] = articles
            content_hash = _compute_content_hash(articles)
            story_hashes[story_id] = content_hash
            tier, _ = _depth(story_id)

            cached = repo.get_cached(story_id, content_hash, model, depth_tier=tier)
            if cached:
                logger.info(
                    "ensure_story_summaries REUSE    run=%-4s story=%-20s tier=%-8s hash=%s",
                    ranking_run_id, story_id, tier, content_hash,
                )
                stories_to_reuse.append({
                    "story_id": story_id,
                    "ranking_run_id": ranking_run_id,
                    "content_hash": content_hash,
                    "headline": cached["headline"],
                    "summary_text": cached["summary_text"],
                    "why_it_matters": cached["why_it_matters"],
                    "audio_script": cached["audio_script"],
                    "model": model,
                    "summary_version": cached["summary_version"],
                    "confidence": cached["confidence"],
                    "depth_tier": tier,
                })
            else:
                stories_to_generate.append(story_id)

    counts["reused"] = len(stories_to_reuse)
    logger.info(
        "ensure_story_summaries run=%s: reuse=%d  llm_calls_needed=%d  already_failed=%d",
        ranking_run_id, counts["reused"], len(stories_to_generate), counts["failed"],
    )

    # ── Phase 2: Parallel LLM calls ──────────────────────────────────────────
    # No DB session held open during network calls.

    generated: list[dict[str, Any]] = []

    if stories_to_generate:
        def _call_llm(story_id: str) -> tuple[str, Any]:
            arts = story_articles[story_id]
            tier, words = _depth(story_id)
            return story_id, summarise_story(
                story_id=story_id,
                representative_title=arts[0].get("title") or "",
                articles=arts,
                category=story_categories.get(story_id),
                depth_words=words,
                depth_tier=tier,
            )

        n_workers = min(len(stories_to_generate), _MAX_PARALLEL_SUMMARIES)
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            for story_id, result in pool.map(_call_llm, stories_to_generate):
                counts["attempted"] += 1
                if result is None:
                    logger.warning(
                        "ensure_story_summaries FAILED   run=%-4s story=%s",
                        ranking_run_id, story_id,
                    )
                    counts["failed"] += 1
                    continue
                logger.info(
                    "ensure_story_summaries GENERATED run=%-4s story=%-20s conf=%.2f  headline=%r",
                    ranking_run_id, story_id, result.confidence, result.headline[:60],
                )
                generated.append({
                    "story_id": story_id,
                    "ranking_run_id": ranking_run_id,
                    "content_hash": story_hashes[story_id],
                    "headline": result.headline,
                    "summary_text": result.summary_text,
                    "why_it_matters": result.why_it_matters or None,
                    "audio_script": result.audio_script or None,
                    "model": model,
                    "summary_version": 1,
                    "confidence": result.confidence,
                    "depth_tier": _depth(story_id)[0],
                })
                counts["generated"] += 1

    # ── Phase 3: Persist ─────────────────────────────────────────────────────

    all_rows = stories_to_reuse + generated
    if all_rows:
        with SessionLocal() as db:
            for row in all_rows:
                db.execute(text("""
                    INSERT INTO data.story_summaries (
                        story_id, ranking_run_id, content_hash,
                        headline, summary_text, why_it_matters, audio_script,
                        model, summary_version, confidence, depth_tier
                    ) VALUES (
                        :story_id, :ranking_run_id, :content_hash,
                        :headline, :summary_text, :why_it_matters, :audio_script,
                        :model, :summary_version, :confidence, :depth_tier
                    )
                    ON CONFLICT (story_id, ranking_run_id, depth_tier) DO NOTHING
                """), row)
            db.commit()

    logger.info(
        "ensure_story_summaries run=%s DONE: attempted=%d generated=%d reused=%d failed=%d",
        ranking_run_id,
        counts["attempted"], counts["generated"], counts["reused"], counts["failed"],
    )
    return counts


# The eager event handler (handle_rank_completed) + its DATA_SUMMARISE_COMPLETED emit were
# removed (audit §6/§7 B.3): summarisation is LAZY — ensure_story_summaries above is called
# inline at bulletin-assembly time in routes/data.py for the stories a briefing selects.


# Bump when the summary SPEC changes (depth word targets / prompt), to invalidate cached
# summaries so they regenerate at the new length instead of serving the old short ones.
# The cache key is (story_id, content_hash, model, depth_tier) and depth_tier is the tier
# NAME — unchanged when only the word target changes — so the version must live in the hash.
_SUMMARY_SPEC_VERSION = "2"  # v2: depth targets ~doubled for ~10-min Medium (was ~5.5 min)


def _compute_content_hash(articles: list[dict]) -> str:
    """SHA-256 fingerprint of the summary spec version + top-5 article titles + snippets."""
    parts = [f"spec:{_SUMMARY_SPEC_VERSION}"]
    for a in articles[:5]:
        title = (a.get("title") or "").strip()
        snippet = (a.get("snippet") or "")[:300].strip()
        parts.append(f"{title}|{snippet}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]
