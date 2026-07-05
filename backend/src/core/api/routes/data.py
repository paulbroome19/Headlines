import asyncio
import json as _json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel
from uuid import uuid4

from sqlalchemy import text
from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo
from core.pipeline.data.ingest.events import DATA_INGEST_REQUESTED
from core.pipeline.ranking.repos.ranking_run_repo import RankingRunRepo
from core.pipeline.ranking.candidate_loader import load_story_ranking_candidates
from core.pipeline.ranking.scorer import score_story
from core.pipeline.ranking.category_ranker import get_category_weight
from core.pipeline.ranking.thresholds import cap_bulletin, qualify_and_order
from core.pipeline.ranking.depth import depth_for_rank
from core.pipeline.ranking.config import DEFAULT_PRESET
from core.pipeline.data.summarise.repos.story_summary_repo import StorySummaryRepo
from core.pipeline.data.bulletin.repos.bulletin_repo import BulletinRepo
from core.pipeline.data.bulletin.repos.user_story_state_repo import (
    UserStoryStateRepo,
    compute_new_state,
)
from core.pipeline.data.bulletin.assembler import assemble
from core.pipeline.data.bulletin.connective import generate_connective, uk_now
from core.pipeline.data.bulletin.edition import resolve_daily_edition
from core.pipeline.data.bulletin.editorial_review import apply_editorial, editorial_adjustments
from core.pipeline.data.bulletin.event_dedup import dedup_same_event  # TACTICAL #35 — remove when #36 lands
from core.pipeline.data.bulletin.selector import (
    compute_request_hash,
    validate_filter_categories,
    select_stories,
    select_stories_by_duration,
    estimate_duration_seconds,
)
from core.pipeline.data.bulletin.audio.tts_client import (
    concatenate_mp3,
    compute_script_hash,
    ext_from_format,
    mp3_duration,
    tts_provider,
    tts_voice,
    tts_model,
    tts_audio_format,
)
from core.pipeline.data.bulletin.audio.segment_synth import synthesize_segments, synthesize_segment
from core.pipeline.data.summarise.handlers.rank_completed import ensure_story_summaries
from core.pipeline.data.bulletin.audio.repos.segment_audio_repo import SegmentAudioRepo
from core.pipeline.data.bulletin.audio.repos.segment_audio_failure_repo import SegmentAudioFailureRepo
from core.pipeline.data.bulletin.audio.repos.bulletin_audio_repo import BulletinAudioRepo
from core.platform.storage.audio_storage import get_audio_storage_provider
from core.platform.config.voices import get_available_voices, get_voice, Voice

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/data", tags=["data"])

_SEGMENT_POLL_INTERVAL = 0.25   # seconds between cache-check polls
_SEGMENT_POLL_MAX      = 40     # 40 × 0.25s = 10s max wait

# "Safe to start" playback once the first N STORIES are fully synthesised — every
# segment up to and including the Nth story segment (intro + those stories + the
# transitions between them). Two stories buffered gives real-time speech a head-start
# so the rest keep generating in the background and stream in with no mid-briefing gap
# when the earlier stories finish. Tunable.
_SAFE_START_STORIES = 2

# Maximum ranked stories to summarise on-demand per Generate press.
# Covers a 5-min bulletin (typically 5–6 stories) with headroom for category filtering.
_SUMMARISE_BUDGET = 20   # = MAX_BULLETIN_STORIES so a full Deep briefing is never truncated

# The daily-edition refill pool is now the FULL qualifier reservoir (every story clearing
# the preset bar in filter order), so after dropping heard/skipped stories the edition
# rebalances from deeper candidates instead of shrinking. No fixed reservoir size needed.


@router.get("/categories")
def get_categories():
    from core.pipeline.data.normalise.categorise.category_loader import load_category_groups
    return {"groups": load_category_groups()}


@router.get("/voices")
def get_voices(refresh: bool = False):
    voices = get_available_voices(force_refresh=refresh)
    return {"voices": [
        {
            "key": v.key,
            "display_name": v.display_name,
            "provider": v.provider,
            "voice_id": v.voice_id,
            "category": v.category,
            "preview_url": v.preview_url,
        }
        for v in voices
    ]}


def _resolve_voice_id(voice_key: str | None) -> str:
    """Validate voice key and return the corresponding voice_id.

    None → server default (tts_voice() from settings).
    Unknown key → HTTPException 422 with list of currently available keys.
    """
    if voice_key is None:
        return tts_voice()
    v = get_voice(voice_key)
    if v is None:
        available = [voice.key for voice in get_available_voices()]
        raise HTTPException(
            status_code=422,
            detail=f"Unknown voice key {voice_key!r}. Available keys: {available}",
        )
    return v.voice_id


@router.post("/ingest/test")
def ingest_test():
    with SessionLocal() as db:
        outbox = OutboxRepo(db)
        outbox.add_event(
            Event(
                type=DATA_INGEST_REQUESTED,
                idempotency_key=f"ingest:{uuid4()}",
                payload={"source": "manual-test"},
            )
        )
        db.commit()

    return {"status": "queued"}


@router.get("/ranking/latest")
def get_latest_ranking():
    with SessionLocal() as db:
        run = RankingRunRepo(db).get_latest()

    if run is None:
        raise HTTPException(status_code=404, detail="No ranking runs found")

    return {
        "id": run["id"],
        "batch_id": run["batch_id"],
        "candidate_count": run["candidate_count"],
        "created_at": run["created_at"].isoformat() if hasattr(run["created_at"], "isoformat") else run["created_at"],
        "top_stories": run["top_stories"],
        "briefing": run["briefing"],
    }


@router.get("/summaries/latest")
def get_latest_summaries():
    with SessionLocal() as db:
        run = RankingRunRepo(db).get_latest()
        if run is None:
            raise HTTPException(status_code=404, detail="No ranking runs found")

        # Build ranking-order from the run's JSONB columns
        top = run["top_stories"] if isinstance(run["top_stories"], list) else _json.loads(run["top_stories"])
        briefing = run["briefing"] if isinstance(run["briefing"], list) else _json.loads(run["briefing"])
        seen: set[str] = set()
        ordered_ids: list[str] = []
        for s in top + briefing:
            sid = str(s["story_id"])
            if sid not in seen:
                seen.add(sid)
                ordered_ids.append(sid)

        summaries = StorySummaryRepo(db).get_by_ranking_run(run["id"], ordered_ids=ordered_ids)

    if not summaries:
        raise HTTPException(status_code=404, detail="No summaries for latest ranking run")

    created_at = run["created_at"]
    return {
        "ranking_run_id": run["id"],
        "ranking_batch_id": run["batch_id"],
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
        "summary_count": len(summaries),
        "summaries": [
            {
                "story_id": s["story_id"],
                "headline": s["headline"],
                "summary_text": s["summary_text"],
                "why_it_matters": s["why_it_matters"],
                "audio_script": s["audio_script"],
                "confidence": s["confidence"],
                "model": s["model"],
                "summary_version": s["summary_version"],
            }
            for s in summaries
        ],
    }


@router.get("/bulletins/latest")
def get_latest_bulletin():
    with SessionLocal() as db:
        bulletin = BulletinRepo(db).get_latest()

    if bulletin is None:
        raise HTTPException(status_code=404, detail="No bulletins found")

    segments = bulletin["segments"]
    if not isinstance(segments, list):
        segments = _json.loads(segments)

    created_at = bulletin["created_at"]
    return {
        "ranking_run_id": bulletin["ranking_run_id"],
        "request_hash": bulletin["request_hash"],
        "story_count": bulletin["story_count"],
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
        "script": bulletin["script"],
        "segments": segments,
    }


class BulletinRequest(BaseModel):
    include_categories: Optional[list[str]] = None
    exclude_categories: Optional[list[str]] = None
    max_stories: int = 20
    name: Optional[str] = None


class AssembleAudioRequest(BaseModel):
    include_categories: Optional[list[str]] = None
    exclude_categories: Optional[list[str]] = None
    max_duration_minutes: int = 5
    name: Optional[str] = None
    voice_key: Optional[str] = None
    include_top_stories: bool = True


@router.post("/bulletins/assemble")
def assemble_bulletin(req: BulletinRequest):
    # Validate categories
    errors: list[str] = []
    if req.include_categories:
        errors.extend(validate_filter_categories(req.include_categories))
    if req.exclude_categories:
        errors.extend(validate_filter_categories(req.exclude_categories))
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    filters: dict = {}
    if req.include_categories:
        filters["include_categories"] = sorted(req.include_categories)
    if req.exclude_categories:
        filters["exclude_categories"] = sorted(req.exclude_categories)
    if req.max_stories != 20:
        filters["max_stories"] = req.max_stories
    if req.name:
        filters["name"] = req.name.strip()
    request_hash = compute_request_hash(filters)

    with SessionLocal() as db:
        run = RankingRunRepo(db).get_latest()
        if run is None:
            raise HTTPException(status_code=404, detail="No ranking runs found")

        ranking_run_id = run["id"]

        # Cache hit
        existing = BulletinRepo(db).get_by_run_and_hash(ranking_run_id, request_hash)
        if existing is not None:
            segments = existing["segments"]
            if not isinstance(segments, list):
                segments = _json.loads(segments)
            created_at = existing["created_at"]
            return {
                "ranking_run_id": ranking_run_id,
                "request_hash": request_hash,
                "story_count": existing["story_count"],
                "cached": True,
                "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
                "script": existing["script"],
                "segments": segments,
            }

        # Build ordered story list from ranking run
        top = run["top_stories"] if isinstance(run["top_stories"], list) else _json.loads(run["top_stories"])
        briefing = run["briefing"] if isinstance(run["briefing"], list) else _json.loads(run["briefing"])
        seen: set[str] = set()
        ordered: list[dict] = []
        for s in top + briefing:
            sid = str(s["story_id"])
            if sid not in seen:
                seen.add(sid)
                ordered.append({"story_id": sid, "primary_category": s.get("primary_category")})

        ordered_ids = [o["story_id"] for o in ordered]
        summaries_by_id = {
            str(s["story_id"]): s
            for s in StorySummaryRepo(db).get_by_ranking_run(ranking_run_id, ordered_ids=ordered_ids)
        }

        all_stories: list[dict] = []
        for item in ordered:
            sid = item["story_id"]
            summary = summaries_by_id.get(sid)
            if summary is None:
                continue
            all_stories.append({
                "story_id": sid,
                "headline": summary.get("headline") or "",
                "audio_script": (summary.get("audio_script") or "").strip() or (summary.get("summary_text") or ""),
                "summary_text": summary.get("summary_text") or "",
                "primary_category": item["primary_category"],
            })

        stories = select_stories(
            all_stories,
            include_categories=req.include_categories or None,
            exclude_categories=req.exclude_categories or None,
            max_stories=req.max_stories,
        )

        if not stories:
            # Normal empty result, not a failure — see the structured code note below.
            raise HTTPException(
                status_code=404,
                detail={"code": "no_stories", "message": "No stories match the requested filters"},
            )

        now_uk = uk_now()
        conn = generate_connective(
            stories=stories,
            name=req.name or None,
            now=now_uk,
            is_first_bulletin=False,
        )
        result = assemble(
            stories,
            seed=int(request_hash, 16),
            name=req.name or None,
            now=now_uk,
            connective=conn,
        )

        BulletinRepo(db).insert(
            ranking_run_id=ranking_run_id,
            request_hash=request_hash,
            filters=filters,
            story_count=len(stories),
            script=result.script,
            segments=result.segments,
        )
        db.commit()

    segments_out = result.segments
    return {
        "ranking_run_id": ranking_run_id,
        "request_hash": request_hash,
        "story_count": len(stories),
        "cached": False,
        "script": result.script,
        "segments": segments_out,
    }


# ── Audio generation ──────────────────────────────────────────────────────────

def _generate_audio(
    bulletin_id: int,
    script: str,
    segments: list[dict],
    *,
    voice_id: str | None = None,
) -> dict[str, Any]:
    """
    Two-level audio cache:
      L1: bulletin_audio — fully assembled MP3 per bulletin/voice/model (no TTS calls)
      L2: segment_audio  — per-segment audio, cross-bulletin and cross-user

    L1 hit → return immediately.
    L1 miss → synthesize_segments() checks L2 per segment, calls TTS only for misses,
              then concatenate_mp3() joins the chunks into the assembled bulletin MP3.

    voice_id must already be resolved (via _resolve_voice_id). Defaults to tts_voice().
    Raises HTTPException on failure.
    """
    provider = tts_provider()
    voice = voice_id or tts_voice()
    model = tts_model()
    audio_fmt = tts_audio_format()
    script_hash = compute_script_hash(script)

    # ── L1: bulletin-level cache ───────────────────────────────────────────
    with SessionLocal() as db:
        cached = BulletinAudioRepo(db).get_cached(
            bulletin_id=bulletin_id,
            script_hash=script_hash,
            provider=provider,
            voice=voice,
            model=model,
            audio_format=audio_fmt,
        )
        if cached is not None:
            logger.info(
                "_generate_audio: L1 hit  bulletin_id=%s  audio_id=%s",
                bulletin_id, cached["id"],
            )
            return {**dict(cached), "cached": True}

    # ── L2: segment-level synthesis (parallel cache fetch + sequential TTS) ─
    # synthesize_segments() checks segment_audio for each segment, parallelises
    # storage fetches for hits, and calls TTS sequentially only for misses.
    segment_chunks = synthesize_segments(
        segments,
        voice=voice,
        model=model,
        audio_format=audio_fmt,
    )

    audio_chunks = [c for c in segment_chunks if c is not None]
    if not audio_chunks:
        raise HTTPException(
            status_code=503,
            detail=f"TTS request failed (provider='{provider}', voice='{voice}') — all segments returned None; check server logs",
        )

    audio_bytes = concatenate_mp3(audio_chunks)

    # ── Persist assembled bulletin audio ───────────────────────────────────
    ext = ext_from_format(audio_fmt)
    filename = f"{bulletin_id}_{script_hash}.{ext}"
    stored = get_audio_storage_provider().store(audio_bytes, filename)

    with SessionLocal() as db:
        audio_id = BulletinAudioRepo(db).insert(
            bulletin_id=bulletin_id,
            script_hash=script_hash,
            provider=provider,
            voice=voice,
            model=model,
            audio_format=audio_fmt,
            storage_path=stored.storage_path,
            audio_url=stored.audio_url,
        )
        db.commit()

    logger.info(
        "_generate_audio: generated  bulletin_id=%s  audio_id=%s  bytes=%d  url=%s",
        bulletin_id, audio_id, len(audio_bytes), stored.audio_url or "(local)",
    )
    return {
        "id": audio_id,
        "bulletin_id": bulletin_id,
        "script_hash": script_hash,
        "provider": provider,
        "voice": voice,
        "model": model,
        "audio_format": audio_fmt,
        "storage_path": stored.storage_path,
        "audio_url": stored.audio_url,
        "duration_seconds": None,
        "cached": False,
    }


@router.post("/bulletins/{bulletin_id}/audio")
def generate_bulletin_audio(bulletin_id: int, voice_key: Optional[str] = None):
    voice_id = _resolve_voice_id(voice_key)

    with SessionLocal() as db:
        bulletin = BulletinRepo(db).get_by_id(bulletin_id)

    if bulletin is None:
        raise HTTPException(status_code=404, detail=f"Bulletin {bulletin_id} not found")

    segs = bulletin["segments"]
    if not isinstance(segs, list):
        segs = _json.loads(segs)
    audio = _generate_audio(bulletin_id, bulletin["script"], segs, voice_id=voice_id)
    audio_id = audio["id"]
    return {
        "bulletin_id": bulletin_id,
        "audio_id": audio_id,
        "script_hash": audio["script_hash"],
        "provider": audio["provider"],
        "voice": audio["voice"],
        "model": audio["model"],
        "audio_format": audio["audio_format"],
        "storage_path": audio["storage_path"],
        "audio_url": audio.get("audio_url"),
        "duration_seconds": audio.get("duration_seconds"),
        "cached": audio["cached"],
    }


def _story_timings(segments: list[dict], script: str, duration: float) -> dict[str, float]:
    """
    Estimate per-story start times (seconds) by proportional character count.
    Uses the same "\n\n" join logic as assembler.assemble() so offsets are exact
    relative to the assembled script — and therefore proportional to the audio.
    """
    total = len(script)
    if total == 0 or duration <= 0:
        return {}
    timings: dict[str, float] = {}
    char_offset = 0
    for seg in segments:
        if seg.get("type") == "story" and seg.get("story_id"):
            timings[str(seg["story_id"])] = round(char_offset / total * duration, 2)
        char_offset += len(seg.get("text", "")) + 2  # +2 matches "\n\n" separator
    return timings


def _preset_from_minutes(max_duration_minutes: float | None) -> str:
    """Map the legacy duration control to a preset (threshold slider). The preset
    is the real control now; an explicit profile `preset` field is the clean
    follow-on. Length is an OUTPUT of what clears the bars, not a target."""
    m = max_duration_minutes or 6
    if m <= 3:
        return "short"
    if m <= 7:
        return "medium"
    return "detailed"


def _select_reservoir(
    db,
    *,
    include_top_stories: bool,
    include_categories: list[str] | None,
    exclude_categories: list[str] | None,
    preset: str,
    ranking_run_id: int | None = None,
) -> list:
    """
    Score fresh candidates (coverage-driven + curated authority/UK-lean), then return the
    full QUALIFIER RESERVOIR for this preset: every story that belongs to a chosen source
    and clears the preset's bar, ordered by the user's FILTER SEQUENCE (top-stories → … →
    sport), within-category by rank. No depth/ceiling cap here — cap_bulletin (or the
    daily edition) applies balance + depth + ceiling on top. Returns `ScoredStory`s so the
    balance step can see scores; excluded categories are filtered out.

    ranking_run_id — enables the LLM EDITORIAL PASS: the top ranked candidates get one
    cheap Haiku review (cached per run) applying bounded category/significance/top-story
    corrections BEFORE selection. Purely additive — omit/disable to skip.
    """
    candidates = load_story_ranking_candidates(db)
    scored = [score_story(c, get_category_weight(c.primary_category)) for c in candidates]

    if ranking_run_id is not None:
        try:
            apply_editorial(scored, editorial_adjustments(db, ranking_run_id, scored))
        except Exception as e:  # never let the editor break assembly
            logging.getLogger(__name__).warning("editorial pass skipped: %s", e)

    reservoir = qualify_and_order(
        scored,
        include_top_stories=include_top_stories,
        include_categories=include_categories,
        preset=preset,
    )

    excludes = list(exclude_categories or [])

    def _excluded(cat: str | None) -> bool:
        if not cat:
            return False
        return any(cat == e or cat.startswith(e + ".") for e in excludes)

    return [s for s in reservoir if not _excluded(s.candidate.primary_category)]


def _cap_to_dicts(display: list) -> tuple[list[dict], dict[str, tuple[str, int]]]:
    """Turn a capped, ordered ScoredStory list into (ordered_story_dicts, depths_by_rank)."""
    ordered: list[dict] = []
    depths: dict[str, tuple[str, int]] = {}
    for s in display:
        sid = s.candidate.story_id
        depths[sid] = depth_for_rank(len(ordered))
        ordered.append({"story_id": sid, "primary_category": s.candidate.primary_category})
    return ordered, depths


def _get_or_assemble_bulletin(
    *,
    profile_id: int | None = None,
    include_categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
    max_duration_minutes: int = 5,
    name: str | None = None,
    include_top_stories: bool = True,
) -> dict[str, Any]:
    """
    Get an existing cached bulletin or assemble a new one.
    Handles story selection only — no audio, no state tracking.

    profile_id: when set, excludes stories already in user_story_state for this
                profile (consumed/rejected/active-queued).

    Returns: bulletin_id, ranking_run_id, request_hash, story_count, script,
             segments, stories_list, bulletin_cached, is_first_bulletin.
    """
    preset = _preset_from_minutes(max_duration_minutes)
    filters: dict = {"preset": preset, "include_top_stories": include_top_stories}
    if include_categories:
        filters["include_categories"] = sorted(include_categories)
    if exclude_categories:
        filters["exclude_categories"] = sorted(exclude_categories)
    if name:
        filters["name"] = name.strip()
    request_hash = compute_request_hash(filters)

    bulletin_cached = False
    stories_list: list[dict] = []
    bulletin_segments: list[dict] = []
    is_first_bulletin = False
    bulletin_id: int = 0
    bulletin_script: str = ""
    story_count: int = 0
    ranking_run_id: int = 0
    # Set only in the non-cached branch of session 1; used after session 1 closes.
    ordered: list[dict] = []
    top_story_ids_set: set[str] = set()
    candidate_ids: list[str] = []
    depths: dict[str, tuple[str, int]] = {}

    # ── Session 1: ranking run lookup + cache check + candidate extraction ────
    with SessionLocal() as db:
        run = RankingRunRepo(db).get_latest()
        if run is None:
            raise HTTPException(status_code=404, detail="No ranking runs found")

        ranking_run_id = run["id"]
        existing_bulletin = BulletinRepo(db).get_by_run_and_hash(ranking_run_id, request_hash)

        if name and existing_bulletin is None:
            prior_count = db.execute(text("""
                SELECT COUNT(*) FROM data.bulletins
                WHERE filters->>'name' = :name
            """), {"name": name.strip()}).scalar()
            is_first_bulletin = (prior_count == 0)

        if existing_bulletin is not None:
            # ── Cached bulletin: read metadata only, no LLM needed ───────────
            bulletin_cached = True
            bulletin_id = existing_bulletin["id"]
            bulletin_script = existing_bulletin["script"]
            story_count = existing_bulletin["story_count"]
            segs = existing_bulletin["segments"]
            if not isinstance(segs, list):
                segs = _json.loads(segs)
            bulletin_segments = segs
            story_ids_cached = [
                str(seg["story_id"])
                for seg in segs
                if seg.get("type") == "story" and seg.get("story_id")
            ]
            if story_ids_cached:
                run_top = run["top_stories"] if isinstance(run["top_stories"], list) else _json.loads(run["top_stories"])
                run_briefing = run["briefing"] if isinstance(run["briefing"], list) else _json.loads(run["briefing"])
                cat_by_id = {str(s["story_id"]): s.get("primary_category") for s in run_top + run_briefing}
                cached_sums = {
                    str(s["story_id"]): s
                    for s in StorySummaryRepo(db).get_by_ranking_run(ranking_run_id, ordered_ids=story_ids_cached)
                }
                stories_list = [
                    {
                        "story_id": sid,
                        "headline": (cached_sums.get(sid) or {}).get("headline") or "",
                        "category": cat_by_id.get(sid),
                    }
                    for sid in story_ids_cached
                ]
        else:
            # ── Non-cached: day-level threshold selection + depth-by-rank ──────
            # Score fresh candidates (coverage-driven), keep everything that clears
            # its source's threshold for this preset, ordered by importance; depth
            # scales with rank. Length is an OUTPUT of what qualifies, not a target.
            # The profile path selects a deeper reservoir (not just the target) so the
            # daily edition can drop already-heard stories and refill from candidates
            # beyond the target instead of shrinking. The non-profile path takes exactly
            # the preset target (its length control).
            reservoir = _select_reservoir(
                db,
                include_top_stories=include_top_stories,
                include_categories=include_categories,
                exclude_categories=exclude_categories,
                preset=preset,
                ranking_run_id=ranking_run_id,
            )

            if profile_id is not None:
                # Stable EDITION OF THE DAY: reuse the persisted per-(profile, day,
                # filters) set instead of re-rolling on every new ranking run. Drops only
                # HEARD/skipped stories (unheard persist), balances the unheard reservoir
                # (cap_bulletin) into the display set, splices a genuinely bigger new lead,
                # and keeps the filter-sequence order. See bulletin/edition.py.
                ordered, depths = resolve_daily_edition(
                    db,
                    profile_id=profile_id,
                    request_hash=request_hash,
                    reservoir=reservoir,
                    include_top_stories=include_top_stories,
                    include_categories=include_categories,
                    preset=preset,
                )
                db.commit()
            else:
                # Non-profile path: balance + depth + ceiling directly on the reservoir.
                ordered, depths = _cap_to_dicts(cap_bulletin(
                    reservoir,
                    include_top_stories=include_top_stories,
                    include_categories=include_categories,
                    preset=preset,
                ))

            candidate_ids = [o["story_id"] for o in ordered[:_SUMMARISE_BUDGET]]

    # ── On-demand summarisation (non-cached path only) ────────────────────────
    # No DB session is held open during LLM calls.
    # Cache-first: only stories with no matching (story_id, content_hash, model)
    # row from any previous run will trigger a paid LLM call.
    if not bulletin_cached:
        ensure_story_summaries(ranking_run_id, candidate_ids, depths)

        # ── Session 2: read summaries, select, assemble, persist ─────────────
        with SessionLocal() as db:
            summaries_by_id = {
                str(s["story_id"]): s
                for s in StorySummaryRepo(db).get_by_ranking_run(ranking_run_id, ordered_ids=candidate_ids)
            }

            all_stories: list[dict] = []
            for o in ordered:
                sid = o["story_id"]
                summary = summaries_by_id.get(sid)
                if summary is None:
                    continue
                all_stories.append({
                    "story_id": sid,
                    "headline": summary.get("headline") or "",
                    "audio_script": (summary.get("audio_script") or "").strip() or (summary.get("summary_text") or ""),
                    "summary_text": summary.get("summary_text") or "",
                    "primary_category": o["primary_category"],
                })

            # Threshold selection already chose + ordered the qualifying stories by
            # importance and assigned each a depth; there is no separate time-budget
            # cut (length is an output of what cleared the bars). Keep them in order.
            stories = all_stories

            if not stories:
                # Normal empty result (the user's filters matched nothing right now),
                # NOT a failure. Structured `code` lets the client show a graceful
                # "all caught up" empty state and distinguish it from a real 404.
                raise HTTPException(
                    status_code=404,
                    detail={"code": "no_stories", "message": "No stories match the requested filters"},
                )

            # TACTICAL #35: collapse same-event duplicates that keyword/hint
            # clustering missed (paraphrased headlines) BEFORE the connective
            # tissue and assembly run — so the event is reported once and the
            # greeting/transitions never repeat it. Conservative; falls back to
            # no-dedup on any failure. Remove when #36 (embeddings) lands.
            stories = dedup_same_event(stories)

            now_uk = uk_now()
            conn = generate_connective(
                stories=stories,
                name=name,
                now=now_uk,
                is_first_bulletin=is_first_bulletin,
            )
            result = assemble(
                stories,
                seed=int(request_hash, 16),
                name=name,
                now=now_uk,
                is_first_bulletin=is_first_bulletin,
                connective=conn,
            )
            bulletin_segments = result.segments

            # Derive stories_list from assembled segments so the order matches
            # the model's chosen running order (mirrors the cache-hit reconstruction path).
            cat_map = {str(s["story_id"]): s.get("primary_category") for s in all_stories}
            ordered_story_ids = [
                str(seg["story_id"])
                for seg in result.segments
                if seg.get("type") == "story" and seg.get("story_id")
            ]
            stories_list = [
                {
                    "story_id": sid,
                    "headline": (summaries_by_id.get(sid) or {}).get("headline") or "",
                    "category": cat_map.get(sid),
                }
                for sid in ordered_story_ids
            ]

            bulletin_id = BulletinRepo(db).insert(
                ranking_run_id=ranking_run_id,
                request_hash=request_hash,
                filters=filters,
                story_count=len(stories),
                script=result.script,
                segments=result.segments,
            )
            bulletin_script = result.script
            story_count = len(stories)
            db.commit()

    return {
        "bulletin_id": bulletin_id,
        "ranking_run_id": ranking_run_id,
        "request_hash": request_hash,
        "story_count": story_count,
        "script": bulletin_script,
        "segments": bulletin_segments,
        "stories_list": stories_list,
        "bulletin_cached": bulletin_cached,
        "is_first_bulletin": is_first_bulletin,
    }


def _do_assemble_and_audio(
    *,
    profile_id: int | None = None,
    include_categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
    max_duration_minutes: int = 5,
    name: str | None = None,
    voice_key: str | None = None,
    include_top_stories: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    voice_id = _resolve_voice_id(voice_key)

    plan = _get_or_assemble_bulletin(
        profile_id=profile_id,
        include_categories=include_categories,
        exclude_categories=exclude_categories,
        max_duration_minutes=max_duration_minutes,
        name=name,
        include_top_stories=include_top_stories,
    )

    bulletin_id     = plan["bulletin_id"]
    bulletin_script = plan["script"]
    bulletin_segments = plan["segments"]
    stories_list    = plan["stories_list"]
    ranking_run_id  = plan["ranking_run_id"]
    bulletin_cached = plan["bulletin_cached"]
    request_hash    = plan["request_hash"]
    story_count     = plan["story_count"]

    # Force: invalidate only the audio cache row for this specific voice.
    if force:
        script_hash = compute_script_hash(bulletin_script)
        with SessionLocal() as db:
            db.execute(text("""
                DELETE FROM data.bulletin_audio
                WHERE bulletin_id  = :bid
                  AND script_hash  = :script_hash
                  AND provider     = :provider
                  AND voice        = :voice
                  AND model        = :model
                  AND audio_format = :audio_format
            """), {
                "bid": bulletin_id,
                "script_hash": script_hash,
                "provider": tts_provider(),
                "voice": voice_id,
                "model": tts_model(),
                "audio_format": tts_audio_format(),
            })
            db.commit()

    audio = _generate_audio(bulletin_id, bulletin_script, bulletin_segments, voice_id=voice_id)

    storage_path = audio.get("storage_path") or ""
    if bulletin_segments and storage_path:
        duration = mp3_duration(storage_path) if os.path.isfile(storage_path) else None
        if duration:
            timings = _story_timings(bulletin_segments, bulletin_script, duration)
            stories_list = [
                {**s, "start_time": timings.get(s["story_id"])}
                for s in stories_list
            ]

    estimated_duration_seconds = round(estimate_duration_seconds(bulletin_script))

    return {
        "ranking_run_id": ranking_run_id,
        "bulletin_id": bulletin_id,
        "request_hash": request_hash,
        "story_count": story_count,
        "target_duration_minutes": max_duration_minutes,
        "estimated_duration_seconds": estimated_duration_seconds,
        "bulletin_cached": bulletin_cached,
        "script": bulletin_script,
        "stories": stories_list,
        "audio_id": audio["id"],
        "script_hash": audio["script_hash"],
        "provider": audio["provider"],
        "voice": audio["voice"],
        "model": audio["model"],
        "audio_format": audio["audio_format"],
        "storage_path": audio["storage_path"],
        "audio_url": audio.get("audio_url"),
        "duration_seconds": audio.get("duration_seconds"),
        "audio_cached": audio["cached"],
    }


@router.post("/bulletins/assemble-and-audio")
def assemble_and_audio(req: AssembleAudioRequest):
    errors: list[str] = []
    if req.include_categories:
        errors.extend(validate_filter_categories(req.include_categories))
    if req.exclude_categories:
        errors.extend(validate_filter_categories(req.exclude_categories))
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    return _do_assemble_and_audio(
        include_categories=req.include_categories,
        exclude_categories=req.exclude_categories,
        max_duration_minutes=req.max_duration_minutes,
        name=req.name,
        voice_key=req.voice_key,
        include_top_stories=req.include_top_stories,
    )


# ── Segment serving ────────────────────────────────────────────────────────────

@router.get("/segments/{segment_hash}.{ext}")
async def serve_segment(segment_hash: str, ext: str):
    """
    Serve a segment's audio, with HONEST, DISTINGUISHABLE states so iOS can tell
    "not ready yet, keep polling" apart from "genuinely failed":

      READY    → 302 redirect to R2 (or FileResponse). Unchanged — current apps
                 follow this exactly as before.
      FAILED   → 502 + JSON {"state":"failed", ...}. The segment's TTS failed
                 after retries (a failure row exists). Returned immediately, no
                 pointless wait. Distinct from "pending".
      PENDING  → 503 + Retry-After + JSON {"state":"pending", ...} after the poll
                 window. Still generating — keep polling. (Kept at 503 so current
                 apps behave exactly as today; the body/Retry-After is additive.)

    Polls the segment_audio cache every 250ms for up to 10s. Async — never holds
    a thread while waiting.
    """
    voice = tts_voice()
    model = tts_model()
    audio_fmt = tts_audio_format()

    for _ in range(_SEGMENT_POLL_MAX):
        with SessionLocal() as db:
            row = SegmentAudioRepo(db).get_cached(
                script_hash=segment_hash, voice=voice, model=model, audio_format=audio_fmt,
            )
            # Only check the failure table when there's no audio yet (keeps the
            # hot READY path a single query).
            failure = None if row else SegmentAudioFailureRepo(db).is_failed(
                script_hash=segment_hash, voice=voice, model=model, audio_format=audio_fmt,
            )

        if row:
            storage_path = row.get("storage_path") or ""
            if storage_path and os.path.isfile(storage_path):
                return FileResponse(storage_path, media_type="audio/mpeg")
            audio_url = row.get("audio_url")
            if audio_url:
                return RedirectResponse(audio_url, status_code=302)

        if failure:
            # GENUINELY FAILED — distinct from "still generating". Return now.
            logger.warning(
                "serve_segment FAILED  hash=%s  attempts=%s  reason=%s",
                segment_hash, failure.get("attempts"), failure.get("last_error"),
            )
            return JSONResponse(
                status_code=502,
                content={
                    "state": "failed",
                    "segment": segment_hash,
                    "reason": failure.get("last_error") or "tts_failed",
                },
            )

        await asyncio.sleep(_SEGMENT_POLL_INTERVAL)

    # Poll window elapsed and not failed → STILL GENERATING. Honest "keep polling".
    return JSONResponse(
        status_code=503,
        content={"state": "pending", "segment": segment_hash},
        headers={"Retry-After": "1"},
    )


@router.get("/bulletins/{bulletin_id}/readiness")
def get_bulletin_readiness(bulletin_id: int):
    """
    Honest readiness/progress signal for the iOS loader gate.

    Reports, for a bulletin iOS already has the id for (from the manifest), which
    segments' audio is READY (cached) vs PENDING (still generating) vs FAILED
    (genuinely failed after retries), plus whether it is SAFE_TO_START playback
    now — the first `_SAFE_START_STORIES` stories (intro + those stories + the
    transitions between them) have audio, so real-time speech stays ahead of
    ~1-2s/segment background generation while the rest stream in.

    iOS polls this ~0.5-1s after the manifest returns to drive a real progress
    bar and flip to playback at `safe_to_start`. Additive — does NOT change the
    manifest or segment contracts iOS already depends on.

    Response (superset of the #58 shape — all original fields unchanged):
      bulletin_id, assembled(true), total_segments, ready_segments,
      failed_segments, segments[{index,type,state}],
      safe_to_start   — THE authoritative "dismiss loader + start audio" signal.
      stage           — ordered: assembled → audio_generating → buffered → ready
                        (or "failed" if a critical segment failed).
      intro_ready, first_story_ready, all_ready, blocked — distinct milestone bools.
      milestones      — ordered [{key, reached, weight}] (weights sum to 1.0) the
                        loader advances a bar against; creep smoothly between them.
      progress        — optional 0–100 hint; never 100 until fully ready and never
                        the dismissal trigger (safe_to_start is).

    NOTE: this covers the post-manifest AUDIO phase. Pre-manifest assembly
    (summarise/connective) runs inside the blocking manifest POST — before the
    client has a bulletin_id — so it is not observable here; a profile-level
    preparation endpoint would be needed to surface those stages.
    """
    voice = tts_voice()
    model = tts_model()
    audio_fmt = tts_audio_format()

    with SessionLocal() as db:
        bulletin = BulletinRepo(db).get_by_id(bulletin_id)
        if bulletin is None:
            raise HTTPException(status_code=404, detail=f"Bulletin {bulletin_id} not found")

        segs = bulletin["segments"]
        if not isinstance(segs, list):
            segs = _json.loads(segs)

        # Reconstruct the manifest's playable segment list: non-empty text, in
        # order (mirrors get_manifest's hashing exactly).
        playable = [s for s in segs if (s.get("text") or "").strip()]
        hashes = [compute_script_hash(s["text"]) for s in playable]

        ready_map = SegmentAudioRepo(db).get_cached_batch(
            script_hashes=hashes, voice=voice, model=model, audio_format=audio_fmt,
        )
        failed_set = SegmentAudioFailureRepo(db).get_failed_batch(
            script_hashes=hashes, voice=voice, model=model, audio_format=audio_fmt,
        )

    ready_set = set(ready_map.keys())
    out_segments: list[dict] = []
    for i, (s, h) in enumerate(zip(playable, hashes)):
        if h in ready_set:
            state = "ready"
        elif h in failed_set:
            state = "failed"
        else:
            state = "pending"
        out_segments.append({"index": i, "type": s.get("type", "story"), "state": state})

    total = len(out_segments)
    ready_count = sum(1 for x in out_segments if x["state"] == "ready")
    failed_count = sum(1 for x in out_segments if x["state"] == "failed")

    # Safe to start once the first _SAFE_START_STORIES stories are ready: play gaplessly
    # through them (intro + those stories + the transitions between) while the remaining
    # stories keep synthesising in the background and stream into the queue.
    story_positions = [i for i, x in enumerate(out_segments) if x["type"] == "story"]
    if len(story_positions) >= _SAFE_START_STORIES:
        lead = story_positions[_SAFE_START_STORIES - 1] + 1   # through the Nth story segment
    elif story_positions:
        lead = story_positions[-1] + 1                        # fewer stories than N → all of them
    else:
        lead = total                                          # no story segments → the whole thing
    safe_to_start = lead > 0 and all(out_segments[i]["state"] == "ready" for i in range(lead))

    # ── Named audio-phase milestones (ordered) the loader advances a bar against ──
    def _first(pred):
        return next((x for x in out_segments if pred(x)), None)

    intro_seg = _first(lambda x: x["type"] == "intro")
    first_story_seg = _first(lambda x: x["type"] == "story")
    # No intro segment (shouldn't happen) → nothing to wait for.
    intro_ready = (intro_seg is None) or intro_seg["state"] == "ready"
    first_story_ready = first_story_seg is not None and first_story_seg["state"] == "ready"
    all_ready = total > 0 and ready_count == total
    audio_started = ready_count > 0
    # A CRITICAL segment (intro or first story) genuinely failed → safe_to_start can
    # never be reached, so the loader would hold forever. Surface it so the client
    # can show an error instead of hanging at ~95%.
    blocked = (intro_seg is not None and intro_seg["state"] == "failed") or (
        first_story_seg is not None and first_story_seg["state"] == "failed"
    )

    # Single ordered stage (furthest milestone reached) — convenient for the client.
    if blocked:
        stage = "failed"
    elif all_ready:
        stage = "ready"
    elif safe_to_start:
        stage = "buffered"       # dismiss-eligible: first 2 stories buffered
    elif audio_started:
        stage = "audio_generating"
    else:
        stage = "assembled"

    # Ordered milestones with weights (sum = 1.0) so the client can drive a bar.
    milestones = [
        {"key": "assembled",         "reached": True,              "weight": 0.10},
        {"key": "audio_generating",  "reached": audio_started,     "weight": 0.15},
        {"key": "intro_ready",       "reached": intro_ready,       "weight": 0.30},
        {"key": "first_story_ready", "reached": first_story_ready, "weight": 0.30},
        {"key": "safe_to_start",     "reached": safe_to_start,     "weight": 0.15},
    ]

    # Optional 0–100 hint: milestone structure blended with audio-completion
    # fraction so it moves both as milestones land AND as segments finish. Capped
    # below 100 until everything is ready — dismissal is driven by safe_to_start,
    # never by this number.
    reached_weight = sum(m["weight"] for m in milestones if m["reached"])
    seg_fraction = (ready_count / total) if total else 0.0
    if all_ready:
        progress = 100
    else:
        progress = min(99, round(100 * (0.6 * reached_weight + 0.4 * seg_fraction)))

    return {
        "bulletin_id": bulletin_id,
        "assembled": True,
        # ── #58 fields (unchanged, still authoritative) ──
        "total_segments": total,
        "ready_segments": ready_count,
        "failed_segments": failed_count,
        "safe_to_start": safe_to_start,       # THE dismiss-loader + start-audio signal
        "segments": out_segments,
        # ── added: ordered milestones the loader advances against ──
        "stage": stage,                       # assembled → audio_generating → buffered → ready (| failed)
        "intro_ready": intro_ready,
        "first_story_ready": first_story_ready,
        "all_ready": all_ready,
        "blocked": blocked,                   # critical segment failed → safe_to_start unreachable
        "milestones": milestones,
        "progress": progress,                 # 0–100 hint (nice-to-have; safe_to_start still dismisses)
    }


# ── Bulletin event tracking ────────────────────────────────────────────────────

class BulletinEventRequest(BaseModel):
    profile_id: int
    story_hash: str
    action: str        # started | completed | skipped | abandoned
    position_pct: float = 0.0


class BulletinSummaryRequest(BaseModel):
    profile_id: int
    events: list[BulletinEventRequest]


@router.post("/bulletins/{bulletin_id}/event")
def record_bulletin_event(bulletin_id: int, req: BulletinEventRequest):
    """
    Record a single story segment event from iOS playback.

    State transitions (CONSUMPTION_THRESHOLD = 0.5):
      completed                → consumed
      skipped  pos >= 0.5      → consumed
      skipped  pos <  0.5      → rejected
      abandoned pos >= 0.5     → consumed
      abandoned pos <  0.5     → no change (stays queued)

    consumed and rejected are terminal — the WHERE state='queued' guard in
    transition_state() prevents any overwrite.
    """
    if req.action not in ("started", "completed", "skipped", "abandoned"):
        raise HTTPException(status_code=422, detail=f"Unknown action {req.action!r}")

    new_state = compute_new_state(req.action, req.position_pct)
    if new_state is None:
        return {"status": "no_change"}

    with SessionLocal() as db:
        updated = UserStoryStateRepo(db).transition_state(
            profile_id=req.profile_id,
            story_hash=req.story_hash,
            new_state=new_state,
        )
        db.commit()

    return {"status": "updated" if updated else "no_change"}


@router.post("/bulletins/{bulletin_id}/summary")
def record_bulletin_summary(bulletin_id: int, req: BulletinSummaryRequest):
    """
    Batch state-transition endpoint called on app backgrounding/closure.
    Applies all outstanding events that didn't fire live (network failures,
    crashes). Idempotent — already-terminal rows are silently skipped.
    """
    events = [e.model_dump() for e in req.events]
    with SessionLocal() as db:
        updated = UserStoryStateRepo(db).transition_batch(
            profile_id=req.profile_id,
            events=events,
        )
        db.commit()

    return {"status": "ok", "updated": updated}