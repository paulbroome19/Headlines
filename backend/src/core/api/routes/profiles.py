import logging
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel

from sqlalchemy import text
from core.platform.db.session import SessionLocal
from core.platform.config.settings import settings
from core.platform.config.voices import get_available_voices, get_voice
from core.pipeline.data.profile.repos.profile_repo import ProfileRepo
from core.pipeline.data.bulletin.repos.user_story_state_repo import UserStoryStateRepo
from core.pipeline.data.bulletin.selector import validate_filter_categories, estimate_duration_seconds
from core.pipeline.data.bulletin.audio.tts_client import (
    compute_script_hash,
    ext_from_format,
    tts_voice,
    tts_model,
    tts_audio_format,
)
from core.pipeline.data.bulletin.audio.segment_synth import synthesize_segment
from core.pipeline.data.bulletin.audio.repos.segment_audio_repo import SegmentAudioRepo
from core.api.routes.data import _do_assemble_and_audio, _get_or_assemble_bulletin
from core.platform.config.source_credibility import rank_sources

# How many ranked outlets to surface under the now-playing card (no "+N").
_SOURCE_ATTRIBUTION_LIMIT = 3

router = APIRouter(prefix="/data/profiles", tags=["profiles"])

logger = logging.getLogger(__name__)


def _prepare_first_bulletin(profile_id: int) -> None:
    """
    Warm a new user's FIRST bulletin in the background, right after onboarding.

    The greeting is unique per user (name + first-bulletin phrasing) so it can't be
    cached and takes ~14s to TTS-generate. If we wait until tap-to-play, the player
    can reach the not-yet-ready greeting and skip it ("no greeting" race). Generating
    the bulletin now — assembly + per-segment audio — means the greeting and first
    story are already cached by the time the user reaches Home and taps play, so
    readiness is (usually) instant.

    Reuses the existing assemble+TTS path (`_do_assemble_and_audio`) verbatim — this
    just triggers it early. The bulletin is cached by (ranking_run_id, request_hash),
    so the tap-time manifest returns the SAME greeting and finds its audio ready.

    Fails silently: if anything goes wrong the normal tap-to-play path still works as
    the fallback. Runs exactly once per profile creation (no loop / over-generation).
    """
    try:
        with SessionLocal() as db:
            profile = ProfileRepo(db).get_by_id(profile_id)
        if profile is None:
            logger.warning("prepare_first_bulletin: profile %s not found — skipping", profile_id)
            return

        logger.info("prepare_first_bulletin: warming first bulletin for profile %s", profile_id)
        _do_assemble_and_audio(
            profile_id=profile_id,
            include_categories=profile["include_categories"],
            exclude_categories=profile["exclude_categories"],
            max_duration_minutes=profile.get("max_duration_minutes", 5),
            name=profile["name"],
            voice_key=profile["voice"],
            include_top_stories=profile.get("include_top_stories", True),
        )
        logger.info("prepare_first_bulletin: done for profile %s", profile_id)
    except Exception as e:  # never let warm-up failure surface — tap-to-play is the fallback
        logger.warning(
            "prepare_first_bulletin: failed for profile %s (%s) — tap-to-play remains the fallback",
            profile_id, e,
        )


class ProfileCreate(BaseModel):
    name: str
    include_categories: Optional[list[str]] = None
    exclude_categories: Optional[list[str]] = None
    max_duration_minutes: int = 5
    voice: Optional[str] = None
    include_top_stories: bool = True


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    include_categories: Optional[list[str]] = None
    exclude_categories: Optional[list[str]] = None
    max_duration_minutes: Optional[int] = None
    voice: Optional[str] = None
    include_top_stories: Optional[bool] = None


def _validate_cats(inc, exc) -> list[str]:
    errors: list[str] = []
    if inc:
        errors.extend(validate_filter_categories(inc))
    if exc:
        errors.extend(validate_filter_categories(exc))
    return errors


def _validate_voice(key: str | None) -> str | None:
    if key is None:
        return None
    if get_voice(key) is None:
        available = [v.key for v in get_available_voices()]
        raise HTTPException(
            status_code=422,
            detail=f"Unknown voice key {key!r}. Available keys: {available}",
        )
    return key


def _fmt(p: dict) -> dict[str, Any]:
    created_at = p["created_at"]
    updated_at = p["updated_at"]
    return {
        "id": p["id"],
        "name": p["name"],
        "include_categories": p["include_categories"],
        "exclude_categories": p["exclude_categories"],
        "max_duration_minutes": p.get("max_duration_minutes", 5),
        "voice": p["voice"],
        "include_top_stories": p.get("include_top_stories", True),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
    }


@router.post("")
def create_profile(
    req: ProfileCreate,
    background_tasks: BackgroundTasks,
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
):
    errors = _validate_cats(req.include_categories, req.exclude_categories)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    voice = _validate_voice(req.voice)

    with SessionLocal() as db:
        repo = ProfileRepo(db)
        # One profile per device: if this device already has one (e.g. re-onboarding
        # after a reinstall recovery missed), UPDATE it rather than create a duplicate.
        existing = repo.get_by_device_id(x_device_id) if x_device_id else None
        if existing is not None:
            profile = repo.update(existing["id"], {
                "name": req.name,
                "include_categories": req.include_categories,
                "exclude_categories": req.exclude_categories,
                "max_duration_minutes": req.max_duration_minutes,
                "voice": voice,
                "include_top_stories": req.include_top_stories,
            })
        else:
            profile = repo.create(
                name=req.name,
                include_categories=req.include_categories,
                exclude_categories=req.exclude_categories,
                max_duration_minutes=req.max_duration_minutes,
                voice=voice,
                include_top_stories=req.include_top_stories,
                device_id=x_device_id,
            )
        db.commit()

    # Warm this user's first bulletin (greeting + audio) in the background so it's
    # ready by the time they reach Home and tap play. Non-blocking; fails silently.
    background_tasks.add_task(_prepare_first_bulletin, profile["id"])

    return _fmt(profile)


@router.get("")
def list_profiles(x_device_id: str | None = Header(default=None, alias="X-Device-Id")):
    # Identity scoping: a client only ever sees ITS OWN device's profile. A request
    # without a device id gets nothing (never the whole table) — this is what stops a
    # fresh/reinstalled device from adopting another tester's profile.
    if not x_device_id:
        return {"profiles": []}
    with SessionLocal() as db:
        profile = ProfileRepo(db).get_by_device_id(x_device_id)
    return {"profiles": [_fmt(profile)] if profile else []}


@router.get("/{profile_id}")
def get_profile(profile_id: int):
    with SessionLocal() as db:
        profile = ProfileRepo(db).get_by_id(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")
    return _fmt(profile)


@router.put("/{profile_id}")
def update_profile(profile_id: int, req: ProfileUpdate):
    updates = {k: v for k, v in req.model_dump().items() if k in req.model_fields_set}
    errors = _validate_cats(updates.get("include_categories"), updates.get("exclude_categories"))
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    if "voice" in updates:
        updates["voice"] = _validate_voice(updates["voice"])

    with SessionLocal() as db:
        profile = ProfileRepo(db).update(profile_id, updates)
        if profile is None:
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")
        db.commit()

    return _fmt(profile)


@router.post("/{profile_id}/bulletin")
def get_profile_bulletin(profile_id: int, force: bool = False):
    if force and settings.env != "dev":
        raise HTTPException(status_code=403, detail="force=true is only permitted in dev")

    with SessionLocal() as db:
        profile = ProfileRepo(db).get_by_id(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")

    result = _do_assemble_and_audio(
        profile_id=profile_id,
        include_categories=profile["include_categories"],
        exclude_categories=profile["exclude_categories"],
        max_duration_minutes=profile.get("max_duration_minutes", 5),
        name=profile["name"],
        voice_key=profile["voice"],
        include_top_stories=profile.get("include_top_stories", True),
        force=force,
    )
    return {"profile_id": profile_id, "profile_name": profile["name"], **result}


# ── Manifest (streaming playback) ─────────────────────────────────────────────


class ManifestRequest(BaseModel):
    include_top_stories: bool = True


@router.post("/{profile_id}/manifest")
def get_manifest(
    profile_id: int,
    req: ManifestRequest,
    background_tasks: BackgroundTasks,
):
    """
    Return a bulletin manifest immediately, triggering background audio generation
    for any uncached segments. The bulletin opens directly with the greeting; iOS
    begins playing the first segment as soon as its audio is ready.

    Flow:
      1. TTL cleanup  — delete stale queued rows (>24h) before assembly
      2. Assembly     — _get_or_assemble_bulletin() with story exclusion
      3. Queued rows  — insert one row per story; COMMIT before step 4
      4. Cache check  — single batch query for all segment hashes
      5. Background   — enqueue synthesize_segment() for each cache miss
      6. Return       — manifest with predicted URLs and duration estimates
    """
    with SessionLocal() as db:
        profile = ProfileRepo(db).get_by_id(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")

    voice = tts_voice() if not profile["voice"] else (
        lambda v: v.voice_id if v else tts_voice()
    )(get_voice(profile["voice"]))
    model     = tts_model()
    audio_fmt = tts_audio_format()
    ext       = ext_from_format(audio_fmt)

    # Step 1: TTL cleanup — must precede assembly so stale queued rows don't
    # block eligible stories from being selected.
    with SessionLocal() as db:
        deleted = UserStoryStateRepo(db).cleanup_stale_queued(profile_id)
        db.commit()
    if deleted:
        import logging as _log
        _log.getLogger(__name__).info(
            "manifest: TTL cleanup removed %d stale queued rows for profile %d",
            deleted, profile_id,
        )

    # Steps 2+3: Assembly with story exclusion baked in.
    plan = _get_or_assemble_bulletin(
        profile_id=profile_id,
        include_categories=profile["include_categories"],
        exclude_categories=profile["exclude_categories"],
        max_duration_minutes=profile.get("max_duration_minutes", 5),
        name=profile["name"],
        include_top_stories=req.include_top_stories,
    )

    # Step 3: Insert queued rows — COMMITTED before any background tasks start.
    # iOS may fire a 'started' event as soon as the manifest is received.
    story_segs = [
        {
            "story_id": int(seg["story_id"]),
            "story_hash": compute_script_hash(seg["text"]),
        }
        for seg in plan["segments"]
        if seg.get("type") == "story" and seg.get("story_id") and (seg.get("text") or "").strip()
    ]
    with SessionLocal() as db:
        UserStoryStateRepo(db).insert_queued_batch(
            profile_id=profile_id,
            bulletin_id=plan["bulletin_id"],
            story_segments=story_segs,
        )
        db.commit()

    # Step 4: Single batch cache check for all non-empty segments.
    all_texts = [
        seg["text"] for seg in plan["segments"] if (seg.get("text") or "").strip()
    ]
    all_hashes = [compute_script_hash(t) for t in all_texts]
    with SessionLocal() as db:
        cached_map = SegmentAudioRepo(db).get_cached_batch(
            script_hashes=all_hashes,
            voice=voice,
            model=model,
            audio_format=audio_fmt,
        )

    # Step 5: Enqueue background TTS for every cache miss.
    for text_val, h in zip(all_texts, all_hashes):
        if h not in cached_map:
            seg_type = next(
                (s.get("type", "story") for s in plan["segments"] if (s.get("text") or "") == text_val),
                "story",
            )
            background_tasks.add_task(
                synthesize_segment,
                text_val,
                segment_type=seg_type,
                voice=voice,
                model=model,
                audio_format=audio_fmt,
            )

    # Step 6: Build manifest.
    base_url = settings.public_api_base_url.rstrip("/")

    # Build title lookup from stories_list for story segments
    titles_by_story_id = {
        str(s["story_id"]): s.get("headline") or ""
        for s in plan.get("stories_list", [])
    }

    # Source attribution: rank each story's outlets by curated credibility and
    # surface the top 2–3 under the now-playing card (BBC before a minor blog).
    story_ids_for_sources = [
        str(seg["story_id"])
        for seg in plan["segments"]
        if seg.get("type") == "story" and seg.get("story_id")
    ]
    sources_by_story_id: dict[str, list[str]] = {}
    if story_ids_for_sources:
        with SessionLocal() as db:
            src_rows = db.execute(text("""
                SELECT story_id::text AS sid, source
                FROM data.normalisation_articles
                WHERE story_id::text = ANY(:ids)
                  AND source IS NOT NULL AND source <> '' AND source <> 'unknown'
            """), {"ids": story_ids_for_sources}).fetchall()
        raw_by_story: dict[str, list[str]] = {}
        for sid, src in src_rows:
            raw_by_story.setdefault(sid, []).append(src)
        sources_by_story_id = {
            sid: rank_sources(srcs, limit=_SOURCE_ATTRIBUTION_LIMIT)
            for sid, srcs in raw_by_story.items()
        }

    # The bulletin opens directly with the greeting — no "Headlines." sting.
    manifest_segments: list[dict[str, Any]] = []

    for i, seg in enumerate(plan["segments"], start=0):
        text_val = seg.get("text") or ""
        if not text_val.strip():
            continue
        h   = compute_script_hash(text_val)
        row = cached_map.get(h)
        duration_ms = (
            round(row["duration_seconds"] * 1000)
            if row and row.get("duration_seconds")
            else round(estimate_duration_seconds(text_val) * 1000)
        )
        item: dict[str, Any] = {
            "index":       i,
            "type":        seg.get("type", "story"),
            "url":         f"{base_url}/data/segments/{h}.{ext}",
            "duration_ms": duration_ms,
        }
        if seg.get("type") == "story" and seg.get("story_id"):
            item["story_hash"] = h
            item["story_id"]   = seg["story_id"]
            item["title"]      = titles_by_story_id.get(str(seg["story_id"]), "")
            item["sources"]    = sources_by_story_id.get(str(seg["story_id"]), [])
        manifest_segments.append(item)

    return {
        "bulletin_id":    plan["bulletin_id"],
        "ranking_run_id": plan["ranking_run_id"],
        "segments":       manifest_segments,
    }
