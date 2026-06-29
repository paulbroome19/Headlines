import asyncio
import json as _json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from uuid import uuid4

from sqlalchemy import text
from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo
from core.pipeline.data.ingest.events import DATA_INGEST_REQUESTED
from core.pipeline.ranking.repos.ranking_run_repo import RankingRunRepo
from core.pipeline.data.summarise.repos.story_summary_repo import StorySummaryRepo
from core.pipeline.data.bulletin.repos.bulletin_repo import BulletinRepo
from core.pipeline.data.bulletin.repos.user_story_state_repo import (
    UserStoryStateRepo,
    compute_new_state,
)
from core.pipeline.data.bulletin.assembler import assemble
from core.pipeline.data.bulletin.connective import generate_connective, uk_now
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
from core.pipeline.data.bulletin.audio.repos.bulletin_audio_repo import BulletinAudioRepo
from core.platform.storage.audio_storage import get_audio_storage_provider
from core.platform.config.voices import get_available_voices, get_voice, Voice

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/data", tags=["data"])

_SEGMENT_POLL_INTERVAL = 0.25   # seconds between cache-check polls
_SEGMENT_POLL_MAX      = 40     # 40 × 0.25s = 10s max wait

# Maximum ranked stories to summarise on-demand per Generate press.
# Covers a 5-min bulletin (typically 5–6 stories) with headroom for category filtering.
_SUMMARISE_BUDGET = 12


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
            raise HTTPException(status_code=404, detail="No stories match the requested filters")

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
    filters: dict = {"max_duration_minutes": max_duration_minutes, "include_top_stories": include_top_stories}
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
            # ── Non-cached: extract candidate list before session closes ──────
            # Summaries don't exist yet — they will be generated after this session.
            top = run["top_stories"] if isinstance(run["top_stories"], list) else _json.loads(run["top_stories"])
            briefing = run["briefing"] if isinstance(run["briefing"], list) else _json.loads(run["briefing"])
            seen: set[str] = set()
            for s in top + briefing:
                sid = str(s["story_id"])
                if sid not in seen:
                    seen.add(sid)
                    ordered.append({"story_id": sid, "primary_category": s.get("primary_category")})

            if profile_id is not None:
                excluded_ids = UserStoryStateRepo(db).get_excluded_story_ids(profile_id)
                if excluded_ids:
                    ordered = [o for o in ordered if int(o["story_id"]) not in excluded_ids]

            top_story_ids_set = {str(s["story_id"]) for s in top}
            candidate_ids = [o["story_id"] for o in ordered[:_SUMMARISE_BUDGET]]

    # ── On-demand summarisation (non-cached path only) ────────────────────────
    # No DB session is held open during LLM calls.
    # Cache-first: only stories with no matching (story_id, content_hash, model)
    # row from any previous run will trigger a paid LLM call.
    if not bulletin_cached:
        ensure_story_summaries(ranking_run_id, candidate_ids)

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

            if include_top_stories:
                top_pool = [s for s in all_stories if s["story_id"] in top_story_ids_set]
                top_picked = select_stories_by_duration(
                    top_pool,
                    max_duration_minutes=max_duration_minutes,
                )
                top_used = sum(
                    estimate_duration_seconds(
                        (s.get("audio_script") or "").strip() or (s.get("summary_text") or "")
                    )
                    for s in top_picked
                )
                remaining_minutes = max_duration_minutes - top_used / 60
                if remaining_minutes > 0:
                    briefing_pool = [s for s in all_stories if s["story_id"] not in top_story_ids_set]
                    briefing_picked = select_stories_by_duration(
                        briefing_pool,
                        include_categories=include_categories or None,
                        exclude_categories=exclude_categories or None,
                        max_duration_minutes=remaining_minutes,
                    )
                    stories = top_picked + briefing_picked
                else:
                    stories = top_picked
            else:
                stories = select_stories_by_duration(
                    all_stories,
                    include_categories=include_categories or None,
                    exclude_categories=exclude_categories or None,
                    max_duration_minutes=max_duration_minutes,
                )

            if not stories and all_stories and (include_categories or exclude_categories):
                logger.warning(
                    "_get_or_assemble_bulletin: no stories matched category filter "
                    "(include=%s, exclude=%s, profile=%s) — falling back to all eligible stories",
                    include_categories, exclude_categories, profile_id,
                )
                stories = select_stories_by_duration(
                    all_stories, max_duration_minutes=max_duration_minutes
                )

            if not stories:
                raise HTTPException(status_code=404, detail="No stories match the requested filters")

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
    Serve a cached segment audio file.

    For cache hits: serves immediately from local storage or redirects to S3/CDN.
    For in-flight generation: polls the segment_audio DB row every 250ms for up
    to 10s. Returns 503 if generation does not complete within the timeout.

    This endpoint is async so it never holds a thread while waiting.
    """
    voice = tts_voice()
    model = tts_model()
    audio_fmt = tts_audio_format()

    for _ in range(_SEGMENT_POLL_MAX):
        with SessionLocal() as db:
            row = SegmentAudioRepo(db).get_cached(
                script_hash=segment_hash,
                voice=voice,
                model=model,
                audio_format=audio_fmt,
            )
        if row:
            storage_path = row.get("storage_path") or ""
            if storage_path and os.path.isfile(storage_path):
                return FileResponse(storage_path, media_type="audio/mpeg")
            audio_url = row.get("audio_url")
            if audio_url:
                return RedirectResponse(audio_url, status_code=302)
        await asyncio.sleep(_SEGMENT_POLL_INTERVAL)

    raise HTTPException(
        status_code=503,
        detail=f"Segment {segment_hash} not yet available — generation timed out",
    )


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