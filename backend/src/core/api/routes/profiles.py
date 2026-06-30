from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
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

router = APIRouter(prefix="/data/profiles", tags=["profiles"])


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
def create_profile(req: ProfileCreate):
    errors = _validate_cats(req.include_categories, req.exclude_categories)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    voice = _validate_voice(req.voice)

    with SessionLocal() as db:
        profile = ProfileRepo(db).create(
            name=req.name,
            include_categories=req.include_categories,
            exclude_categories=req.exclude_categories,
            max_duration_minutes=req.max_duration_minutes,
            voice=voice,
            include_top_stories=req.include_top_stories,
        )
        db.commit()

    return _fmt(profile)


@router.get("")
def list_profiles():
    with SessionLocal() as db:
        profiles = ProfileRepo(db).get_all()
    return {"profiles": [_fmt(p) for p in profiles]}


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
        manifest_segments.append(item)

    return {
        "bulletin_id":    plan["bulletin_id"],
        "ranking_run_id": plan["ranking_run_id"],
        "segments":       manifest_segments,
    }
