import asyncio
import concurrent.futures
import json as _json
import logging
import os
import random
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
from core.pipeline.ranking.ranked_list import scored_from_ranked_list
from core.pipeline.ranking.thresholds import cap_bulletin, qualify_and_order
from core.pipeline.ranking.depth import depth_for_rank, depth_tier_for_rank
from core.pipeline.ranking.config import DEFAULT_PRESET
from core.pipeline.data.profile.repos.profile_repo import ProfileRepo
from core.platform.config.settings import settings
from core.platform.config.source_credibility import rank_sources
from core.pipeline.data.summarise.repos.story_summary_repo import StorySummaryRepo
from core.pipeline.data.bulletin.repos.bulletin_repo import BulletinRepo
from core.pipeline.data.bulletin.repos.user_story_state_repo import (
    UserStoryStateRepo,
    compute_new_state,
)
from core.pipeline.data.bulletin.assembler import assemble, template_bridges
from core.pipeline.data.bulletin.connective import ConnectiveResult
from core.pipeline.data.bulletin.connective import (
    generate_connective, generate_greeting, generate_bridges_and_outro, uk_now,
)
from core.pipeline.data.bulletin.outros import build_outro
from core.pipeline.data.bulletin.selection import (
    build_selection,
    get_materialised_selection,
    resolve_materialised_selection,
)
from core.pipeline.data.bulletin.edition import UserDailyEditionRepo
from core.pipeline.data.bulletin.event_dedup import dedup_same_event  # TACTICAL #35 — remove when #36 lands
from core.pipeline.data.bulletin.selector import (
    compute_request_hash,
    selection_filters,
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
from core.pipeline.data.bulletin.audio.segment_synth import (
    synthesize_segments, synthesize_segment, synthesize_parallel,
)
from core.pipeline.data.summarise.handlers.rank_completed import ensure_story_summaries, _compute_content_hash
from core.pipeline.data.bulletin.audio.repos.segment_audio_repo import SegmentAudioRepo
from core.pipeline.data.bulletin.audio.repos.segment_audio_failure_repo import SegmentAudioFailureRepo
from core.pipeline.data.bulletin.audio.segment_watchdog import is_stalled
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
_SAFE_START_STORIES = 2   # start-pack SIZE: intro + story 1 + story 2 synthesised together
_SAFE_START_GATE = 1      # playback START gate: intro + story 1 ready (story 2 lands in parallel,
                          #                      well before story 1's audio ends)

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


# Home-preview minutes estimate — CALIBRATED to real generated briefings (measured audio
# duration + actual summary word counts on prod), NOT the summariser's DEPTH_TIERS word
# TARGETS. The summariser undershoots its targets (a "lead" targets 260 words but actually
# runs ~110–150), so estimating from the targets overshot real duration by ~40% on small
# briefings. These per-rank-band word figures + the measured ~150 wpm speech rate track real
# briefings within ~1 min and scale properly. Exact minutes still come from the generated
# bulletin.
_HOME_PREVIEW_WPM = 150
# Estimated spoken words per story by rank band (lead / major / standard / brief), from the
# measured audio_script lengths of recent real briefings.
_PREVIEW_WORDS_BY_TIER = {"lead": 150, "major": 130, "standard": 95, "brief": 58}
_PREVIEW_INTRO_WORDS = 60
_PREVIEW_OUTRO_WORDS = 40
_PREVIEW_TRANSITION_WORDS = 16   # per inter-story bridge


def _preview_minutes(story_count: int) -> int:
    """Estimated briefing minutes for `story_count` stories: calibrated per-rank story words
    (measured, not the aspirational depth targets) + intro/outro/transition wrapper, at the
    measured speech rate. Tracks real briefings within ~1 min and scales with count/depth."""
    if story_count <= 0:
        return 0
    story_words = sum(_PREVIEW_WORDS_BY_TIER[depth_tier_for_rank(i)] for i in range(story_count))
    wrapper_words = (
        _PREVIEW_INTRO_WORDS + _PREVIEW_OUTRO_WORDS
        + _PREVIEW_TRANSITION_WORDS * max(0, story_count - 1)
    )
    return max(1, round((story_words + wrapper_words) / _HOME_PREVIEW_WPM))


def _standfirst(summary_text: str | None) -> str | None:
    """A short newspaper standfirst from a cached summary — the first sentence (or a
    ~160-char trim at a word boundary). None when there's no cached summary yet."""
    s = (summary_text or "").strip()
    if not s:
        return None
    first = s.split(". ", 1)[0].strip()
    if 40 <= len(first) <= 200:
        return first if first.endswith((".", "!", "?")) else first + "."
    if len(s) <= 180:
        return s
    cut = s[:160].rsplit(" ", 1)[0].rstrip(",;: ")
    return cut + "…"


def _coherent_standfirst(
    summary_text: str | None, summary_content_hash: str | None, current_content_hash: str | None
) -> str | None:
    """Guard the headline↔standfirst mismatch (audit risk #3). The preview headline is the LIVE
    representative article; the standfirst is the latest cached summary. If that summary was built
    from different articles than the story's current top-5 (content_hash differs — the
    representative shifted, e.g. an over-merged cluster moved to another event), SUPPRESS the
    standfirst rather than show text about a different event. When either hash is missing we can't
    compare, so keep the standfirst (back-compat)."""
    if summary_content_hash and current_content_hash and summary_content_hash != current_content_hash:
        return None
    return _standfirst(summary_text)


@router.get("/profiles/{profile_id}/home-preview")
def get_home_preview(profile_id: int):
    """
    Cheap, READ-ONLY Home front-page preview: the real top-ranked stories for the user's
    SELECTED categories (headline + standfirst + sources + category) plus an estimated
    total minutes and the selected story count — WITHOUT generating audio, calling any
    LLM, or writing user_story_state.

    Reads the SAME materialised (deduped) selection the briefing assembles from
    (resolve_materialised_selection), so `story_count` and the tiles are exactly the stories
    that will play — one list, one count. dedup runs on representative titles (no summaries,
    no audio), so the preview stays cheap and LLM-light. Duration is a faithful estimate from
    the depth word model (never seconds); exact minutes come from the generated briefing.
    """
    with SessionLocal() as db:
        profile = ProfileRepo(db).get_by_id(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")

    preset = _preset_from_minutes(profile.get("max_duration_minutes", 5))
    include_top_stories = profile.get("include_top_stories", True)   # D4: respect the profile
    include_categories = profile["include_categories"]
    exclude_categories = list(profile.get("exclude_categories") or [])
    headline_by_id: dict[str, str] = {}

    # ONE materialised selection — byte-identical to what the briefing will open with. The
    # request_hash MUST be built the same way here and in the manifest/assemble paths, or the
    # skeleton won't find this stored edition (see selection_filters / the #157 wiring bug).
    filters = selection_filters(
        preset=preset, include_top_stories=include_top_stories,
        include_categories=include_categories, exclude_categories=exclude_categories,
        name=profile.get("name"),
    )
    request_hash = compute_request_hash(filters, day=uk_now().date())
    with SessionLocal() as db:
        ordered, _depths, _run = resolve_materialised_selection(
            db, profile_id=profile_id, request_hash=request_hash,
            include_top_stories=include_top_stories, include_categories=include_categories,
            exclude_categories=exclude_categories, preset=preset,
        )
    cat_by_id = {o["story_id"]: o.get("primary_category") for o in ordered}

    story_count = len(ordered)
    # Minutes estimate calibrated to real briefings (measured words/duration), not the
    # summariser's word targets — see _preview_minutes.
    total_minutes = _preview_minutes(story_count)

    # Join standfirst (cached summaries) + ranked sources for the displayed stories only.
    ids = [o["story_id"] for o in ordered][:6]
    standfirst_by_id: dict[str, str | None] = {}
    sources_by_id: dict[str, list[str]] = {}
    if ids:
        with SessionLocal() as db:
            sum_rows = db.execute(text("""
                SELECT DISTINCT ON (story_id::text) story_id::text AS sid, summary_text, content_hash
                FROM data.story_summaries
                WHERE story_id::text = ANY(:ids) AND summary_text IS NOT NULL
                ORDER BY story_id::text, created_at DESC
            """), {"ids": ids}).fetchall()
            # The story's CURRENT top-5 articles (same shape/order the summariser hashes) — so we
            # can tell whether the cached summary still describes the current representative.
            art_rows = db.execute(text("""
                SELECT sid, title, content_snippet FROM (
                    SELECT story_id::text AS sid, title, content_snippet,
                           row_number() OVER (PARTITION BY story_id
                                              ORDER BY published_at DESC NULLS LAST) AS rn
                    FROM data.normalisation_articles
                    WHERE story_id::text = ANY(:ids)
                ) q WHERE rn <= 5 ORDER BY sid, rn
            """), {"ids": ids}).fetchall()
            src_rows = db.execute(text("""
                SELECT story_id::text AS sid, source
                FROM data.normalisation_articles
                WHERE story_id::text = ANY(:ids)
                  AND source IS NOT NULL AND source <> '' AND source <> 'unknown'
            """), {"ids": ids}).fetchall()
        # Recompute each story's current content_hash and only keep a standfirst whose cached
        # summary was built from that same content (coherence guard — audit risk #3).
        arts_by_story: dict[str, list[dict]] = {}
        for sid, title, snippet in art_rows:
            arts_by_story.setdefault(sid, []).append({"title": title, "snippet": snippet})
        # Headline = the LIVE representative (most-recent) article title — for the materialised
        # path this is the only source of the title (the stored selection is ids only).
        for sid, arts in arts_by_story.items():
            headline_by_id.setdefault(sid, (arts[0]["title"] if arts else "") or "")
        current_hash_by_id = {sid: _compute_content_hash(arts) for sid, arts in arts_by_story.items()}
        standfirst_by_id = {
            sid: _coherent_standfirst(txt, chash, current_hash_by_id.get(sid))
            for sid, txt, chash in sum_rows
        }
        raw_by_story: dict[str, list[str]] = {}
        for sid, src in src_rows:
            raw_by_story.setdefault(sid, []).append(src)
        sources_by_id = {sid: rank_sources(srcs, limit=3) for sid, srcs in raw_by_story.items()}

    stories = [
        {
            "story_id": sid,
            "headline": headline_by_id.get(sid, ""),
            "category": cat_by_id.get(sid),
            "standfirst": standfirst_by_id.get(sid),
            "sources": sources_by_id.get(sid, []),
        }
        for sid in ids
    ]

    return {
        "profile_id": profile_id,
        "name": profile.get("name"),
        "story_count": story_count,
        "total_minutes": total_minutes,
        "stories": stories,
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
    request_hash = compute_request_hash(filters, day=uk_now().date())

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
            # A split lead has two story segments sharing one story_id; keep the FIRST (the
            # opener's start) so the story's start time is where it actually begins.
            timings.setdefault(str(seg["story_id"]), round(char_offset / total * duration, 2))
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
    filters = selection_filters(
        preset=preset, include_top_stories=include_top_stories,
        include_categories=include_categories, exclude_categories=exclude_categories, name=name,
    )
    request_hash = compute_request_hash(filters, day=uk_now().date())

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
            # The ONE materialised selection (deduped, capped) — byte-identical to what the home
            # preview showed. Both preview and briefing consume resolve_materialised_selection;
            # it drops heard/skipped, dedups, caps, and pins the day's ranking run.
            ordered, depths, _pin = resolve_materialised_selection(
                db, profile_id=profile_id, request_hash=request_hash,
                include_top_stories=include_top_stories, include_categories=include_categories,
                exclude_categories=exclude_categories, preset=preset,
            )

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

            # (Same-event dedup already ran in the materialised selection, before cap — TACTICAL
            # #35, in bulletin.selection. Remove that when #36 (embeddings) lands.)
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

            # ONE membership: assembly may have dropped a selected story that had no summary for
            # this run (a story is only appended above when its summary exists). Reconcile the
            # edition to the ASSEMBLED set so the home preview (screen) shows exactly what plays —
            # screen == skeleton == audio. Removal-only (assembled ⊆ pinned edition); no-op if the
            # profile has no materialised edition.
            if profile_id is not None:
                UserDailyEditionRepo(db).reconcile_story_ids(
                    profile_id, now_uk.date(), request_hash,
                    [str(s["story_id"]) for s in stories],
                )
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


# ── Streaming assembly ───────────────────────────────────────────────────────────
# The manifest returns a bulletin_id after SELECTION only; summarise + connective + TTS run
# in the background, prioritising intro + story 1 + story 2 so safe_to_start@2 flips in
# ~15-25s (parallel start-pack TTS) instead of ~80s. A skeleton row (script='') marks the
# bulletin in-progress; /readiness fills in per-segment as texts land and audio synthesises.

_STREAM_PRIORITY_TTL = 3600   # seconds a skip-priority hint lives


def _stream_redis():
    try:
        from redis import Redis
        return Redis.from_url(settings.redis_url, socket_timeout=1)
    except Exception:
        return None


def set_synth_priority(bulletin_id: int, story_id: str) -> None:
    """Skip-reprioritise hook (wired now; the skip UI arrives in the follow-up PR): mark a
    story to jump the synthesis queue. `_synth_remaining` polls this before each segment."""
    r = _stream_redis()
    if r is None:
        return
    try:
        r.set(f"synth:prio:{bulletin_id}", str(story_id), ex=_STREAM_PRIORITY_TTL)
    except Exception:
        pass


def _get_synth_priority(bulletin_id: int) -> str | None:
    r = _stream_redis()
    if r is None:
        return None
    try:
        v = r.get(f"synth:prio:{bulletin_id}")
        return v.decode() if isinstance(v, (bytes, bytearray)) else (str(v) if v else None)
    except Exception:
        return None


def _skeleton_segments(db, ordered: list[dict]) -> list[dict]:
    """Provisional segment list mirroring assemble()'s structure (intro, story, [transition,
    story]..., outro) with EMPTY texts. Story segments carry story_id + title (from
    data.stories) so the manifest shows the running order immediately, before summaries."""
    story_ids = [str(o["story_id"]) for o in ordered]
    titles: dict[str, str] = {}
    if story_ids:
        rows = db.execute(text("""
            SELECT id::text AS sid, representative_title FROM data.stories
            WHERE id::text = ANY(:ids)
        """), {"ids": story_ids}).fetchall()
        titles = {sid: (t or "") for sid, t in rows}
    segs: list[dict] = [{"type": "intro", "text": ""}]
    for i, o in enumerate(ordered):
        sid = str(o["story_id"])
        title = titles.get(sid, "")
        if i > 0:
            segs.append({"type": "transition", "text": ""})
        if i == 0:
            # LEAD is split into opener + remainder (same story_id) in assemble(); reserve BOTH
            # slots here so the manifest structure — and every segment index the iOS client and
            # /readiness agree on — matches the finished bulletin from the very first fetch.
            segs.append({"type": "story", "story_id": o["story_id"], "title": title, "text": "", "lead_part": "opener"})
            segs.append({"type": "story", "story_id": o["story_id"], "title": title, "text": "", "lead_part": "remainder"})
        else:
            segs.append({"type": "story", "story_id": o["story_id"], "title": title, "text": ""})
    segs.append({"type": "outro", "text": ""})
    return segs


def prepare_bulletin_for_manifest(
    *,
    profile_id: int | None,
    include_categories: list[str] | None,
    exclude_categories: list[str] | None,
    max_duration_minutes: int,
    name: str | None,
    include_top_stories: bool,
) -> dict:
    """Selection + cache check for the streaming manifest. Returns one of:
      {'mode': 'cached'}     — a fully-assembled bulletin exists; caller uses the normal
                               full-manifest build (unchanged behaviour).
      {'mode': 'skeleton', 'dispatch': bool, 'bulletin_id', 'ranking_run_id', 'segments',
       'bg'?}                — a skeleton row (new → dispatch=True with bg args; or an
                               in-progress row from a concurrent request → dispatch=False).
    Fast: no LLM work here — just selection + a skeleton INSERT."""
    preset = _preset_from_minutes(max_duration_minutes)
    filters = selection_filters(
        preset=preset, include_top_stories=include_top_stories,
        include_categories=include_categories, exclude_categories=exclude_categories, name=name,
    )
    request_hash = compute_request_hash(filters, day=uk_now().date())

    with SessionLocal() as db:
        run = RankingRunRepo(db).get_latest()
        if run is None:
            raise HTTPException(status_code=404, detail="No ranking runs found")
        ranking_run_id = run["id"]

        existing = BulletinRepo(db).get_by_run_and_hash(ranking_run_id, request_hash)
        if existing is not None and (existing.get("script") or "").strip():
            return {"mode": "cached"}                      # fully assembled → normal path
        if existing is not None:
            segs = existing["segments"]
            if not isinstance(segs, list):
                segs = _json.loads(segs)
            return {"mode": "skeleton", "dispatch": False,  # in-progress → join, don't re-run
                    "bulletin_id": existing["id"], "ranking_run_id": ranking_run_id, "segments": segs}

        is_first = False
        if name:
            prior = db.execute(
                text("SELECT COUNT(*) FROM data.bulletins WHERE filters->>'name' = :n"),
                {"n": name.strip()},
            ).scalar()
            is_first = (prior == 0)

        # SKELETON selection. The materialised (deduped) selection is the source of truth for both
        # the home preview and the FINAL briefing. In the common flow the user loaded the preview
        # first, so it already exists → the skeleton READS it and is identical to the final, with no
        # LLM on the first-play path. Only on a COLD play-first (warmer / deep-link, no preview yet)
        # do we fall back to the fast PRE-DEDUP order (build_selection(dedup=False)); the background
        # then materialises the deduped final. The LEAD is identical either way — dedup keeps the
        # best-ranked member of each group, so it can never drop index 0 (opener-gate safe).
        mat = (get_materialised_selection(db, profile_id=profile_id, request_hash=request_hash)
               if profile_id is not None else None)
        if mat is not None:
            ordered, depths, _run = mat
        else:
            ordered = build_selection(
                db, profile_id=profile_id, include_top_stories=include_top_stories,
                include_categories=include_categories, exclude_categories=exclude_categories,
                preset=preset, dedup=False,
            )
            depths = {o["story_id"]: depth_for_rank(i) for i, o in enumerate(ordered)}

        if not ordered:
            raise HTTPException(
                status_code=404,
                detail={"code": "no_stories", "message": "No stories match the requested filters"},
            )

        candidate_ids = [o["story_id"] for o in ordered[:_SUMMARISE_BUDGET]]
        skeleton = _skeleton_segments(db, ordered)
        bulletin_id = BulletinRepo(db).insert(
            ranking_run_id=ranking_run_id, request_hash=request_hash, filters=filters,
            story_count=len(ordered), script="", segments=skeleton,
        )
        db.commit()

    return {
        "mode": "skeleton", "dispatch": True, "bulletin_id": bulletin_id,
        "ranking_run_id": ranking_run_id, "segments": skeleton,
        "bg": {
            "bulletin_id": bulletin_id, "ranking_run_id": ranking_run_id, "request_hash": request_hash,
            "name": name, "is_first_bulletin": is_first, "profile_id": profile_id,
            "ordered": ordered, "depths": depths, "candidate_ids": candidate_ids,
            # filter params so the bg can build the deduped FINAL selection (dedup-before-cap,
            # matching the preview) on a cold play-first — never by deduping the post-cap skeleton.
            "include_top_stories": include_top_stories, "include_categories": include_categories,
            "exclude_categories": exclude_categories, "preset": preset,
        },
    }


def _bg_load_story_dicts(ranking_run_id: int, sids: list[str], cat_by_id: dict[str, Any]) -> list[dict]:
    with SessionLocal() as db:
        sums = {
            str(s["story_id"]): s
            for s in StorySummaryRepo(db).get_by_ranking_run(ranking_run_id, ordered_ids=sids)
        }
    out: list[dict] = []
    for sid in sids:
        s = sums.get(str(sid))
        if not s:
            continue
        out.append({
            "story_id": sid,
            "headline": s.get("headline") or "",
            "audio_script": (s.get("audio_script") or "").strip() or (s.get("summary_text") or ""),
            "summary_text": s.get("summary_text") or "",
            "primary_category": cat_by_id.get(str(sid)),
        })
    return out


def _bg_headlines(ordered: list[dict], limit: int = 3) -> list[str]:
    """Cheap read of the top `limit` stories' representative_title (same source the skeleton
    manifest uses) so the greeting can be generated at T0 from headlines alone — no summaries.
    Returns headlines in `ordered` order; missing titles come back as ''."""
    sids = [str(o["story_id"]) for o in ordered[:limit]]
    if not sids:
        return []
    with SessionLocal() as db:
        rows = db.execute(text("""
            SELECT id::text AS sid, representative_title FROM data.stories
            WHERE id::text = ANY(:ids)
        """), {"ids": sids}).fetchall()
    by = {sid: (t or "") for sid, t in rows}
    return [by.get(sid, "") for sid in sids]


def _fill_prefix_and_synth(bulletin_id: int, prefix_segments: list[dict],
                           voice: str, model: str, audio_fmt: str) -> None:
    """Fill the skeleton's first segments (intro, lead OPENER, lead remainder, transition,
    story 2) with the start-pack texts, then synthesise them GATE-FIRST: the intro + opener
    (the safe_to_start gate) go in the first parallel call so the loader dismisses on ~20-25s
    of audio; the remainder + story 2 stream in behind, before playback catches up."""
    fill = prefix_segments[:-1]  # assemble() of the 2-story pack ends in a start-outro; drop it
    with SessionLocal() as db:
        repo = BulletinRepo(db)
        b = repo.get_by_id(bulletin_id)
        if b is None:
            return
        segs = b["segments"]
        if not isinstance(segs, list):
            segs = _json.loads(segs)
        for i, seg in enumerate(fill):
            if i < len(segs):
                segs[i]["text"] = seg.get("text") or ""
        repo.update_segments(bulletin_id, segs)
        db.commit()
    # GATE FIRST: intro + the lead opener are exactly what safe_to_start waits for. Synthesising
    # them alone first (both short) gives the opener sole first-call priority — it never queues
    # behind the long remainder / story 2 — so the gate flips consistently regardless of the full
    # lead's length. The rest synthesise together next so they land before playback reaches them;
    # if not, the client holds in its buffering state on the pending segment.
    gate = [s for s in fill if s.get("type") == "intro" or s.get("lead_part") == "opener"]
    behind = [s for s in fill if s not in gate]
    if gate:
        synthesize_parallel(gate, voice=voice, model=model, audio_format=audio_fmt)
    if behind:
        synthesize_parallel(behind, voice=voice, model=model, audio_format=audio_fmt)


def _start_pack_boundary(segments: list[dict], n_stories: int) -> int:
    """Count of leading segments the start-pack already synthesised: intro + the first
    `n_stories` distinct stories (a split lead's opener+remainder count as ONE) + the
    transitions between them. Returns the index of the first segment of the (n+1)-th story,
    or len(segments) if there are ≤ n_stories. The transition PRECEDING the (n+1)-th story is
    NOT included (it synthesises with that story). Structural, so it's correct whether or not
    the lead was split."""
    seen: list[str] = []
    end = 0  # one past the last segment belonging to one of the first n_stories
    for i, s in enumerate(segments):
        if s.get("type") == "story" and s.get("story_id"):
            sid = str(s["story_id"])
            if sid not in seen:
                if len(seen) >= n_stories:
                    break
                seen.append(sid)
            end = i + 1
    return end if seen else len(segments)


def _unit_start_index(segments: list[dict]) -> dict[str, int]:
    """story_id → the index of the FIRST segment the client waits on to START that story: its
    preceding TRANSITION (the bridge a tap plays from) when it has one, else the story segment.

    Prioritising from the bridge matters because a tap on a story (offset 0) begins on the bridge,
    so that's the segment the client's readiness/pending check keys off. Pivoting on the story
    segment alone left the bridge in the BACKFILL (synthesised last) while the client sat waiting
    on it — the tap-to-a-distant-story stall. Skip-forward starts past the bridge (on the opener),
    so it's unaffected either way; keying on the bridge fixes tap without regressing skip."""
    out: dict[str, int] = {}
    for i, s in enumerate(segments):
        if s.get("type") == "story" and s.get("story_id"):
            sid = str(s["story_id"])
            if sid not in out:
                out[sid] = i - 1 if i - 1 >= 0 and segments[i - 1].get("type") == "transition" else i
    return out


def _reprioritise_order(remaining: list[int], pidx: int | None) -> list[int]:
    """FORWARD-THEN-BACKFILL: given the still-to-synthesise indices (current order) and the first
    segment index `pidx` of the bumped story unit, return the bumped unit + everything AFTER it,
    then the BACKFILLED earlier gap. No-op if nothing is bumped or the unit is already done (pidx
    not in `remaining`). Example (jump to story 7 of 10): bridge7, 7, 8, 9, 10, then the 3-6 gap.
    Pure/deterministic so the reorder is unit-testable without Redis or a synth call."""
    if pidx is None or pidx not in remaining:
        return remaining
    forward = [i for i in remaining if i >= pidx]
    backfill = [i for i in remaining if i < pidx]
    return forward + backfill


def _synth_remaining(bulletin_id: int, segments: list[dict],
                     voice: str, model: str, audio_fmt: str) -> None:
    """Synthesise every segment after the start-pack, honouring a tap/skip priority hint with the
    forward-then-backfill rule (see _reprioritise_order). The hint is re-read from Redis on EVERY
    iteration and synthesis is strictly SEQUENTIAL (one segment at a time), so a bumped story jumps
    the queue after only the current in-flight call — nothing else competes for it."""
    # The start-pack synthesised intro + the first _SAFE_START_STORIES stories (the lead's
    # opener+remainder count as ONE story) + their transitions — skip exactly those, derived
    # from the structure rather than a hardcoded count (the lead split makes it 5, not 4).
    already = set(range(_start_pack_boundary(segments, _SAFE_START_STORIES)))
    remaining = [i for i in range(len(segments)) if i not in already]
    story_idx = _unit_start_index(segments)
    while remaining:
        prio = _get_synth_priority(bulletin_id)                       # re-read every loop (live)
        remaining = _reprioritise_order(remaining, story_idx.get(prio) if prio else None)
        nxt = remaining.pop(0)
        seg = segments[nxt]
        if (seg.get("text") or "").strip():
            synthesize_segment(seg["text"], segment_type=seg.get("type", "story"),
                               voice=voice, model=model, audio_format=audio_fmt)


def run_background_assembly(*, bulletin_id: int, ranking_run_id: int, request_hash: str,
                            name: str | None, is_first_bulletin: bool,
                            ordered: list[dict], depths: dict, candidate_ids: list[str],
                            profile_id: int | None = None,
                            include_top_stories: bool = True, include_categories: list[str] | None = None,
                            exclude_categories: list[str] | None = None, preset: str = "medium") -> None:
    """Background job (runs in a threadpool as a sync BackgroundTask). SPLIT connective for fast
    first-play: the GREETING is generated ALONE (Haiku, from the lead headline — no summaries) so
    it and the lead opener are the ONLY things on the first-play path; every transition + the outro
    are generated in the background AFTER the start-pack is synthesising, so they never gate. Fills
    the skeleton so /readiness streams; then finalises and synthesises the rest."""
    try:
        voice, model, audio_fmt = tts_voice(), tts_model(), tts_audio_format()
        cat_by_id = {str(o["story_id"]): o.get("primary_category") for o in ordered}
        seed = int(request_hash, 16)
        now_uk = uk_now()
        first_two = [str(s) for s in candidate_ids[:_SAFE_START_STORIES]]

        # 1. GREETING (Haiku) + start-pack summaries, IN PARALLEL. The greeting needs only the lead
        #    headline (read cheaply, no summary), so it fires at T0 and overlaps summarisation —
        #    taking the ~70s connective-Sonnet call OFF the first-play path entirely.
        headlines = _bg_headlines(ordered, limit=3)
        lead_headline = headlines[0] if headlines else ""
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            greet_future = ex.submit(
                generate_greeting,
                lead_headline=lead_headline, trail_headlines=headlines[1:],
                name=name, now=now_uk, is_first_bulletin=is_first_bulletin,
            )
            ensure_story_summaries(ranking_run_id, first_two, depths)  # runs while the greeting generates
            greeting = greet_future.result()

        # 2-3. Assemble the start-pack from the greeting + EMPTY tissue (no Sonnet), then fill +
        #      parallel TTS gate-first (intro + opener). safe_to_start is reachable right here.
        start_stories = _bg_load_story_dicts(ranking_run_id, first_two, cat_by_id)
        if start_stories:
            if greeting:
                start_ids = [str(s["story_id"]) for s in start_stories]
                start_conn = ConnectiveResult(
                    greeting=greeting, order=start_ids,
                    transitions={sid: "" for sid in start_ids},  # bridges deferred to the background
                    outro="",                                    # dropped by _fill_prefix_and_synth anyway
                )
                start_result = assemble(start_stories, seed=seed, name=name, now=now_uk,
                                        is_first_bulletin=is_first_bulletin, connective=start_conn)
            else:
                # Greeting LLM failed → template intro/bridges for the start-pack (unchanged fallback).
                start_result = assemble(start_stories, seed=seed, name=name, now=now_uk,
                                        is_first_bulletin=is_first_bulletin, connective=None)
            _fill_prefix_and_synth(bulletin_id, start_result.segments, voice, model, audio_fmt)

        # 4-5. BACKGROUND (post-gate): summarise the rest, then generate EVERY non-lead bridge +
        #      the outro in one Sonnet call, continuing the greeting's voice. Never gates first-play.
        ensure_story_summaries(ranking_run_id, candidate_ids, depths)

        # FINAL selection = the ONE materialised (deduped) selection, identical to the home preview.
        # Read it if the preview already materialised it; else build+store it now (cold play-first) —
        # in the BACKGROUND, off the first-play path. dedup runs BEFORE cap, so this can differ from
        # deduping the pre-dedup skeleton, which is exactly why we rebuild rather than dedup candidate_ids.
        if profile_id is not None:
            with SessionLocal() as db:
                final_ordered, _fd, _fr = resolve_materialised_selection(
                    db, profile_id=profile_id, request_hash=request_hash,
                    include_top_stories=include_top_stories, include_categories=include_categories,
                    exclude_categories=exclude_categories, preset=preset,
                )
            cat_by_id.update({str(o["story_id"]): o.get("primary_category") for o in final_ordered})
            all_stories = _bg_load_story_dicts(ranking_run_id, [str(o["story_id"]) for o in final_ordered], cat_by_id)
        else:
            all_stories = dedup_same_event(_bg_load_story_dicts(ranking_run_id, [str(s) for s in candidate_ids], cat_by_id))

        # Defensive: dedup keeps the best-ranked member of each group, so the LEAD can never change
        # (deduped[0] == pre_dedup[0]). If it ever does, the start-pack synthesised the wrong opener.
        if all_stories and first_two and str(all_stories[0]["story_id"]) != first_two[0]:
            logger.warning(
                "run_background_assembly: LEAD SHIFTED after dedup (start-pack lead=%s, final lead=%s) "
                "— opener audio may mismatch (should be impossible)", first_two[0], all_stories[0]["story_id"],
            )

        # 6. Full connective → authoritative segments. If the greeting exists, keep it verbatim
        #    (its intro audio is already synthesised) and only fill bridges + outro; on bridge
        #    failure fall back to silent bridges + a template outro. If there was no greeting, the
        #    start-pack used templates, so finish the whole bulletin on the template path too.
        full_conn = None
        if greeting:
            order_ids = [str(s["story_id"]) for s in all_stories]
            bridges = generate_bridges_and_outro(
                stories=all_stories, prior_greeting=greeting, name=name, now=now_uk,
            )
            if bridges is not None:
                transitions, outro = {sid: bridges[0].get(sid, "") for sid in order_ids}, bridges[1]
            else:
                # LLM bridge pass failed (e.g. truncation on a full-size briefing). Fall back to
                # TEMPLATE bridges — NOT silent ones — so the briefing keeps its connective tissue.
                # The greeting is kept verbatim (its intro audio is already synthesised), so we only
                # replace the non-lead transitions + the outro.
                transitions = template_bridges(all_stories, seed=seed)
                outro = build_outro(random.Random(seed), name=name, now=now_uk, story_count=len(all_stories))
            if order_ids:
                transitions[order_ids[0]] = ""  # the lead leads straight from the greeting
            full_conn = ConnectiveResult(greeting=greeting, order=order_ids,
                                         transitions=transitions, outro=outro)
        result = assemble(all_stories, seed=seed, name=name, now=now_uk,
                          is_first_bulletin=is_first_bulletin, connective=full_conn)

        # 7. Finalise the row (segments + non-empty script → 'assembled' cache-hit marker).
        with SessionLocal() as db:
            BulletinRepo(db).update_after_assembly(
                bulletin_id, segments=result.segments, script=result.script, story_count=len(all_stories),
            )
            # ONE membership: all_stories is the summarised (post-drop) set actually assembled, so
            # reconcile the edition to it — the home preview (screen) then matches skeleton + audio.
            # Removal-only; no-op if this profile has no materialised edition (cold play-first).
            if profile_id is not None:
                UserDailyEditionRepo(db).reconcile_story_ids(
                    profile_id, now_uk.date(), request_hash,
                    [str(s["story_id"]) for s in all_stories],
                )
            db.commit()

        # 7a. The start-pack synthesised its bridges as EMPTY placeholders (greeting-split defers
        #     bridge generation). Now that the full connective has filled them, synthesise the
        #     bridges that live INSIDE the start-pack boundary — _synth_remaining skips that range,
        #     so without this the pre-story-2 bridge would stay pending forever.
        boundary = _start_pack_boundary(result.segments, _SAFE_START_STORIES)
        prefix_bridges = [
            s for s in result.segments[:boundary]
            if s.get("type") == "transition" and (s.get("text") or "").strip()
        ]
        if prefix_bridges:
            synthesize_parallel(prefix_bridges, voice=voice, model=model, audio_format=audio_fmt)

        # 7b. Seed heard-tracking (user_story_state) for the STREAMING path. Only the cache-hit
        # manifest (_build_full_manifest) seeds otherwise, so a fresh streaming play — e.g. an
        # edited-filter first play that isn't pre-warmed — would record no heard/skip events.
        # One queued row per story (ON CONFLICT (profile_id, story_id) DO NOTHING → keyed to the
        # lead's opener hash, matching the unit hash iOS fires events with). Idempotent, so it's
        # harmless if a later cache-hit manifest re-seeds the same rows.
        if profile_id is not None:
            story_segs = [
                {"story_id": int(seg["story_id"]), "story_hash": compute_script_hash(seg["text"])}
                for seg in result.segments
                if seg.get("type") == "story" and seg.get("story_id") and (seg.get("text") or "").strip()
            ]
            if story_segs:
                with SessionLocal() as db:
                    UserStoryStateRepo(db).insert_queued_batch(
                        profile_id=profile_id, bulletin_id=bulletin_id, story_segments=story_segs,
                    )
                    db.commit()

        # 8. Synthesise the rest, honouring skip-priority.
        _synth_remaining(bulletin_id, result.segments, voice, model, audio_fmt)
        logger.info("run_background_assembly complete: bulletin=%s stories=%d", bulletin_id, len(all_stories))
    except Exception as e:  # never crash the worker thread
        logger.exception("run_background_assembly failed for bulletin %s: %s", bulletin_id, e)


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

        # Report EVERY segment by its FULL index (position in bulletin.segments), NOT a
        # playable-only re-index — so iOS matches readiness → skeleton segment reliably even
        # when a pause transition has empty text (which would otherwise shift the indices).
        # A non-empty script marks the bulletin fully assembled.
        assembled_final = bool((bulletin.get("script") or "").strip())
        hash_by_idx: dict[int, str] = {
            i: compute_script_hash(s["text"])
            for i, s in enumerate(segs) if (s.get("text") or "").strip()
        }
        ready_map = SegmentAudioRepo(db).get_cached_batch(
            script_hashes=list(hash_by_idx.values()), voice=voice, model=model, audio_format=audio_fmt,
        )
        failed_set = SegmentAudioFailureRepo(db).get_failed_batch(
            script_hashes=list(hash_by_idx.values()), voice=voice, model=model, audio_format=audio_fmt,
        )

    ready_set = set(ready_map.keys())
    base_url = settings.public_api_base_url.rstrip("/")
    ext = ext_from_format(audio_fmt)
    out_segments: list[dict] = []
    for i, s in enumerate(segs):
        typ = s.get("type", "story")
        txt = (s.get("text") or "").strip()
        seg: dict[str, Any] = {"index": i, "type": typ}
        if not txt:
            # Empty pause transition in a FINISHED bulletin → a 0-duration skip (ready);
            # anything empty in a still-assembling bulletin → not-yet-filled (pending).
            if assembled_final and typ == "transition":
                seg["state"] = "ready"
                seg["duration_ms"] = 0
            else:
                seg["state"] = "pending"
        else:
            h = hash_by_idx[i]
            if h in ready_set:
                seg["state"] = "ready"
                seg["url"] = f"{base_url}/data/segments/{h}.{ext}"
                row = ready_map.get(h)
                if row and row.get("duration_seconds"):
                    seg["duration_ms"] = round(row["duration_seconds"] * 1000)
            elif h in failed_set:
                seg["state"] = "failed"
            else:
                seg["state"] = "pending"
        if typ == "story" and s.get("story_id"):
            seg["story_id"] = str(s["story_id"])
        out_segments.append(seg)

    total = len(out_segments)
    ready_count = sum(1 for x in out_segments if x["state"] == "ready")
    failed_count = sum(1 for x in out_segments if x["state"] == "failed")

    # Safe to start once the first _SAFE_START_STORIES stories are ready: play gaplessly
    # through them (intro + those stories + the transitions between) while the remaining
    # stories keep synthesising in the background and stream into the queue.
    story_positions = [i for i, x in enumerate(out_segments) if x["type"] == "story"]
    if len(story_positions) >= _SAFE_START_GATE:
        lead = story_positions[_SAFE_START_GATE - 1] + 1       # through the gate-th story segment
    elif story_positions:
        lead = story_positions[-1] + 1                        # fewer stories than N → all of them
    else:
        lead = total                                          # no story segments → the whole thing

    # STALL WATCHDOG: within the safe-to-start GATE window (the segments the loader waits on, which
    # synthesise FIRST), a segment still 'pending' long after assembly has a dead/hung/restarted
    # synth that never recorded a failure — the loader would otherwise never fire safe_to_start AND
    # never see a failure to surface, so the spinner hangs forever. Flip such gate segments to
    # FAILED (record it so /segments returns 502 too) so the existing failed path fires and iOS can
    # skip. Tail (non-gate) segments are left alone — they legitimately stream in the background.
    _created = bulletin.get("created_at")
    _age_s = (datetime.now(timezone.utc) - _created).total_seconds() if _created else 0.0
    _stalled_idx = [i for i in range(min(lead, total)) if is_stalled(out_segments[i]["state"], _age_s)]
    if _stalled_idx:
        with SessionLocal() as _db:
            _frepo = SegmentAudioFailureRepo(_db)
            for i in _stalled_idx:
                _frepo.record(script_hash=hash_by_idx[i], voice=voice, model=model,
                              audio_format=audio_fmt, attempts=0,
                              last_error="stalled: no synth completion within the watchdog window")
                out_segments[i]["state"] = "failed"
            _db.commit()
        failed_count = sum(1 for x in out_segments if x["state"] == "failed")
        logger.warning("readiness watchdog: bulletin=%s flipped %d stalled gate segment(s) to failed",
                       bulletin_id, len(_stalled_idx))

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
    # Any segment in the safe-to-start GATE window failing (a genuine failure OR a watchdog-reaped
    # stall) means safe_to_start can never fire → surface it so the loader errors/skips instead of
    # hanging. (Broadened from intro+first-story to the whole gate so a stalled 2nd gate story is
    # caught too.)
    blocked = any(out_segments[i]["state"] == "failed" for i in range(min(lead, total)))

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


# ── Tap-to-prioritise (Netflix-style per-story readiness) ───────────────────────

class PrioritiseRequest(BaseModel):
    story_id: str


@router.post("/bulletins/{bulletin_id}/prioritise")
def prioritise_story(bulletin_id: int, req: PrioritiseRequest):
    """Bump a not-yet-synthesised story to the FRONT of the background synthesis queue when
    the listener taps/jumps to it. `_synth_remaining` reads this and applies the
    forward-then-backfill order (bumped story + everything after it, then the earlier gap).
    Best-effort: no-ops gracefully if the bulletin is already fully synthesised or Redis is
    unavailable."""
    set_synth_priority(bulletin_id, str(req.story_id))
    return {"ok": True, "bulletin_id": bulletin_id, "prioritised": str(req.story_id)}


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