import json as _json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
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
from core.pipeline.data.bulletin.assembler import assemble
from core.pipeline.data.bulletin.selector import (
    compute_request_hash,
    validate_filter_categories,
    select_stories,
    select_stories_by_duration,
    estimate_duration_seconds,
)
from core.pipeline.data.bulletin.audio.tts_client import (
    synthesize,
    compute_script_hash,
    ext_from_format,
    mp3_duration,
    tts_provider,
    tts_voice,
    tts_model,
    tts_audio_format,
)
from core.pipeline.data.bulletin.audio.repos.bulletin_audio_repo import BulletinAudioRepo
from core.platform.storage.audio_storage import get_audio_storage_provider
from core.platform.config.voices import get_available_voices, get_voice, Voice

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/data", tags=["data"])


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

        result = assemble(
            stories,
            seed=int(request_hash, 16),
            name=req.name or None,
            now=datetime.now(timezone.utc),
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

def _generate_audio(bulletin_id: int, script: str, *, voice_id: str | None = None) -> dict[str, Any]:
    """
    Cache-check then generate TTS audio for a bulletin.
    voice_id must already be resolved (via _resolve_voice_id). Defaults to tts_voice().
    Returns a dict with audio metadata. Raises HTTPException on failure.
    Never generates audio twice for the same cache key.
    """
    provider = tts_provider()
    voice = voice_id or tts_voice()
    model = tts_model()
    audio_fmt = tts_audio_format()
    script_hash = compute_script_hash(script)

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
                "_generate_audio: cache hit  bulletin_id=%s  audio_id=%s",
                bulletin_id, cached["id"],
            )
            return {**dict(cached), "cached": True}

    # TTS call outside any DB session (no connection held during network call)
    audio_bytes = synthesize(script, provider=provider, voice=voice, model=model, audio_format=audio_fmt)

    if audio_bytes is None:
        raise HTTPException(
            status_code=503,
            detail=f"TTS request failed (provider='{provider}', voice='{voice}') — check server logs for details",
        )

    # Persist via storage provider (local or S3)
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

    audio = _generate_audio(bulletin_id, bulletin["script"], voice_id=voice_id)
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


def _do_assemble_and_audio(
    *,
    include_categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
    max_duration_minutes: int = 5,
    name: str | None = None,
    voice_key: str | None = None,
    include_top_stories: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    # Validate and resolve voice key → voice_id before any DB work.
    voice_id = _resolve_voice_id(voice_key)

    # max_duration_minutes and include_top_stories always in cache key.
    filters: dict = {"max_duration_minutes": max_duration_minutes, "include_top_stories": include_top_stories}
    if include_categories:
        filters["include_categories"] = sorted(include_categories)
    if exclude_categories:
        filters["exclude_categories"] = sorted(exclude_categories)
    if name:
        filters["name"] = name.strip()
    request_hash = compute_request_hash(filters)

    # ── Get or assemble bulletin ───────────────────────────────────────────
    bulletin_cached = False
    stories_list: list[dict] = []
    bulletin_segments: list[dict] = []     # full segment list kept for timing
    with SessionLocal() as db:
        run = RankingRunRepo(db).get_latest()
        if run is None:
            raise HTTPException(status_code=404, detail="No ranking runs found")

        ranking_run_id = run["id"]
        existing_bulletin = BulletinRepo(db).get_by_run_and_hash(ranking_run_id, request_hash)

        if existing_bulletin is not None:
            bulletin_cached = True
            bulletin_id = existing_bulletin["id"]
            bulletin_script = existing_bulletin["script"]
            story_count = existing_bulletin["story_count"]

            # Reconstruct story list for display from stored segments + summaries.
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
                    "audio_script": (summary.get("audio_script") or "").strip() or (summary.get("summary_text") or ""),
                    "summary_text": summary.get("summary_text") or "",
                    "primary_category": item["primary_category"],
                })

            if include_top_stories:
                # Top-tier stories fill first (no category filter), then remaining
                # duration filled from briefing tier with category filter applied.
                top_story_ids_set = {str(s["story_id"]) for s in top}
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

            if not stories:
                raise HTTPException(status_code=404, detail="No stories match the requested filters")

            stories_list = [
                {
                    "story_id": s["story_id"],
                    "headline": (summaries_by_id.get(s["story_id"]) or {}).get("headline") or "",
                    "category": s.get("primary_category"),
                }
                for s in stories
            ]

            result = assemble(
                stories,
                seed=int(request_hash, 16),
                name=name,
                now=datetime.now(timezone.utc),
            )
            bulletin_segments = result.segments

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

    # ── Get or generate audio ──────────────────────────────────────────────
    # Force: invalidate only the audio cache row for this specific voice.
    # Other voices' cached audio is preserved. Bulletin script is unchanged.
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

    audio = _generate_audio(bulletin_id, bulletin_script, voice_id=voice_id)

    # ── Annotate stories with proportional start times ─────────────────────
    # Reads total duration from the local MP3 file (no new TTS calls).
    # Skips gracefully for S3/remote storage where the file isn't local.
    storage_path = audio.get("storage_path") or ""
    if bulletin_segments and storage_path:
        duration = mp3_duration(storage_path) if os.path.isfile(storage_path) else None
        if duration:
            timings = _story_timings(bulletin_segments, bulletin_script, duration)
            stories_list = [
                {**s, "start_time": timings.get(s["story_id"])}
                for s in stories_list
            ]

    # Estimate total bulletin duration from assembled script.
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