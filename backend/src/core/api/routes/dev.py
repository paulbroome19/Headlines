"""
Local development dashboard.

All /dev/api/* endpoints are read-only inspection endpoints — zero pipeline side effects.
The /dev route serves the HTML dashboard itself.

Product endpoints called by the dashboard UI live in routes/data.py.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import text

from core.platform.db.session import SessionLocal

router = APIRouter(prefix="/dev", tags=["dev"])

_DASHBOARD_HTML = Path(__file__).parent / "dev_dashboard.html"


# ── Dashboard HTML ─────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    return HTMLResponse(_DASHBOARD_HTML.read_text())


# ── Inspection: pipeline overview ─────────────────────────────────────────────

@router.get("/api/pipeline")
def inspect_pipeline():
    """
    Read-only summary of the pipeline state:
    recent ranking runs with summary/bulletin/audio counts,
    recent bulletins, recent audio.
    """
    with SessionLocal() as db:
        runs = db.execute(text("""
            SELECT
                rr.id,
                rr.batch_id,
                rr.candidate_count,
                rr.created_at,
                COUNT(DISTINCT ss.id)  AS summary_count,
                ROUND(AVG(ss.confidence)::numeric, 2) AS avg_confidence,
                COUNT(DISTINCT b.id)   AS bulletin_count,
                COUNT(DISTINCT ba.id)  AS audio_count
            FROM data.ranking_runs rr
            LEFT JOIN data.story_summaries ss ON ss.ranking_run_id = rr.id
            LEFT JOIN data.bulletins b        ON b.ranking_run_id  = rr.id
            LEFT JOIN data.bulletin_audio ba  ON ba.bulletin_id    = b.id
            GROUP BY rr.id, rr.batch_id, rr.candidate_count, rr.created_at
            ORDER BY rr.id DESC
            LIMIT 10
        """)).mappings().all()

        bulletins = db.execute(text("""
            SELECT id, ranking_run_id, request_hash, story_count, filters, created_at
            FROM data.bulletins
            ORDER BY id DESC
            LIMIT 10
        """)).mappings().all()

        audio = db.execute(text("""
            SELECT id, bulletin_id, provider, voice, model, audio_format,
                   storage_path, duration_seconds, created_at
            FROM data.bulletin_audio
            ORDER BY id DESC
            LIMIT 10
        """)).mappings().all()

        outbox_summary = db.execute(text("""
            SELECT event_type, status, COUNT(*) as cnt
            FROM event.outbox
            GROUP BY event_type, status
            ORDER BY event_type, status
        """)).mappings().all()

    def _ts(v):
        return v.isoformat() if v and hasattr(v, "isoformat") else v

    def _file_size(path):
        try:
            return os.path.getsize(path) if path and os.path.exists(path) else None
        except Exception:
            return None

    return {
        "ranking_runs": [
            {
                "id": r["id"],
                "batch_id": r["batch_id"],
                "candidate_count": r["candidate_count"],
                "created_at": _ts(r["created_at"]),
                "summary_count": r["summary_count"],
                "avg_confidence": float(r["avg_confidence"]) if r["avg_confidence"] else None,
                "bulletin_count": r["bulletin_count"],
                "audio_count": r["audio_count"],
            }
            for r in runs
        ],
        "bulletins": [
            {
                "id": b["id"],
                "ranking_run_id": b["ranking_run_id"],
                "request_hash": b["request_hash"],
                "story_count": b["story_count"],
                "filters": b["filters"] if isinstance(b["filters"], dict) else json.loads(b["filters"] or "{}"),
                "created_at": _ts(b["created_at"]),
            }
            for b in bulletins
        ],
        "audio_files": [
            {
                "id": a["id"],
                "bulletin_id": a["bulletin_id"],
                "provider": a["provider"],
                "voice": a["voice"],
                "model": a["model"],
                "audio_format": a["audio_format"],
                "storage_path": a["storage_path"],
                "file_size_bytes": _file_size(a["storage_path"]),
                "duration_seconds": a["duration_seconds"],
                "created_at": _ts(a["created_at"]),
            }
            for a in audio
        ],
        "outbox_summary": [dict(r) for r in outbox_summary],
    }


# ── Inspection: stories with articles + ranking position ──────────────────────

@router.get("/api/stories")
def inspect_stories():
    """
    Read-only. Returns stories from the latest ranking run, in ranked order,
    with all articles and categories attached.
    """
    with SessionLocal() as db:
        run = db.execute(text("""
            SELECT id, top_stories, briefing FROM data.ranking_runs ORDER BY id DESC LIMIT 1
        """)).mappings().first()

        if run is None:
            return {"ranking_run_id": None, "stories": []}

        top = run["top_stories"] if isinstance(run["top_stories"], list) else json.loads(run["top_stories"] or "[]")
        briefing = run["briefing"] if isinstance(run["briefing"], list) else json.loads(run["briefing"] or "[]")

        seen: set[str] = set()
        ordered: list[dict] = []
        for tier, items in [("top", top), ("briefing", briefing)]:
            for s in items:
                sid = str(s["story_id"])
                if sid not in seen:
                    seen.add(sid)
                    ordered.append({"story_id": sid, "tier": tier, "primary_category": s.get("primary_category")})

        if not ordered:
            return {"ranking_run_id": run["id"], "stories": []}

        story_ids = [int(o["story_id"]) for o in ordered]

        articles_rows = db.execute(text("""
            SELECT
                na.story_id,
                na.id           AS article_id,
                na.title,
                na.source,
                na.published_at,
                na.category_primary,
                na.category_method,
                e.display_name  AS entity_name,
                e.entity_type,
                nae.is_primary  AS entity_is_primary
            FROM data.normalisation_articles na
            LEFT JOIN LATERAL (
                SELECT nae2.entity_id, nae2.is_primary
                FROM data.normalisation_article_entities nae2
                WHERE nae2.normalisation_article_id = na.id
                ORDER BY nae2.is_primary DESC, nae2.confidence_score DESC NULLS LAST
                LIMIT 1
            ) nae ON true
            LEFT JOIN data.entities e ON e.id = nae.entity_id
            WHERE na.story_id = ANY(:ids)
            ORDER BY na.story_id, na.published_at DESC NULLS LAST
        """), {"ids": story_ids}).mappings().all()

        # Group articles by story_id
        articles_by_story: dict[str, list[dict]] = {}
        for row in articles_rows:
            sid = str(row["story_id"])
            ts = row["published_at"]
            articles_by_story.setdefault(sid, []).append({
                "article_id": row["article_id"],
                "title": row["title"],
                "source": row["source"],
                "published_at": ts.isoformat() if ts and hasattr(ts, "isoformat") else ts,
                "category_primary": row["category_primary"],
                "category_method": row["category_method"],
                "entity_name": row["entity_name"],
                "entity_type": row["entity_type"],
            })

        # Grab story metadata
        story_meta = db.execute(text("""
            SELECT id, representative_title, primary_category, article_count,
                   first_published_at, last_published_at
            FROM data.stories WHERE id = ANY(:ids)
        """), {"ids": story_ids}).mappings().all()
        meta_by_id = {str(r["id"]): r for r in story_meta}

    stories_out = []
    for rank, item in enumerate(ordered, 1):
        sid = item["story_id"]
        meta = meta_by_id.get(sid, {})
        stories_out.append({
            "rank": rank,
            "tier": item["tier"],
            "story_id": sid,
            "primary_category": item["primary_category"],
            "representative_title": meta.get("representative_title"),
            "article_count": len(articles_by_story.get(sid, [])),
            "articles": articles_by_story.get(sid, []),
        })

    return {"ranking_run_id": run["id"], "stories": stories_out}


# ── Inspection: summaries ──────────────────────────────────────────────────────

@router.get("/api/summaries")
def inspect_summaries():
    """
    Read-only. Returns story summaries from the latest ranking run, ranked order.
    Richer than /data/summaries/latest — includes content_hash and all fields.
    """
    with SessionLocal() as db:
        run = db.execute(text("""
            SELECT id, top_stories, briefing FROM data.ranking_runs ORDER BY id DESC LIMIT 1
        """)).mappings().first()

        if run is None:
            return {"ranking_run_id": None, "summaries": []}

        top = run["top_stories"] if isinstance(run["top_stories"], list) else json.loads(run["top_stories"] or "[]")
        briefing = run["briefing"] if isinstance(run["briefing"], list) else json.loads(run["briefing"] or "[]")

        seen: set[str] = set()
        ordered: list[dict] = []
        for tier, items in [("top", top), ("briefing", briefing)]:
            for s in items:
                sid = str(s["story_id"])
                if sid not in seen:
                    seen.add(sid)
                    ordered.append({"story_id": sid, "tier": tier, "category": s.get("primary_category")})

        if not ordered:
            return {"ranking_run_id": run["id"], "summaries": []}

        rows = db.execute(text("""
            SELECT story_id, headline, summary_text, why_it_matters, audio_script,
                   confidence, model, summary_version, content_hash, created_at
            FROM data.story_summaries
            WHERE ranking_run_id = :run_id
        """), {"run_id": run["id"]}).mappings().all()

        by_story = {str(r["story_id"]): r for r in rows}

    out = []
    for rank, item in enumerate(ordered, 1):
        sid = item["story_id"]
        s = by_story.get(sid)
        if s is None:
            continue
        ts = s["created_at"]
        out.append({
            "rank": rank,
            "tier": item["tier"],
            "story_id": sid,
            "category": item["category"],
            "headline": s["headline"],
            "summary_text": s["summary_text"],
            "why_it_matters": s["why_it_matters"],
            "audio_script": s["audio_script"],
            "confidence": float(s["confidence"]) if s["confidence"] is not None else None,
            "model": s["model"],
            "summary_version": s["summary_version"],
            "content_hash": s["content_hash"],
            "word_count": len((s["audio_script"] or "").split()),
            "created_at": ts.isoformat() if ts and hasattr(ts, "isoformat") else ts,
        })

    return {"ranking_run_id": run["id"], "summaries": out}


# ── Inspection: valid categories for filter UI ────────────────────────────────

@router.get("/api/categories")
def inspect_categories():
    """Read-only. Returns all valid category slugs from the YAML taxonomy."""
    from core.pipeline.data.normalise.categorise.category_loader import load_valid_category_slugs
    return sorted(load_valid_category_slugs())


# ── Local dev audio serving ────────────────────────────────────────────────────

@router.get("/api/audio/{bulletin_id}/file", include_in_schema=False)
def serve_bulletin_audio(bulletin_id: int):
    """
    Local dev only — serves the MP3 file for a bulletin's audio.
    Not for production; file paths are local absolute paths.
    """
    with SessionLocal() as db:
        row = db.execute(text("""
            SELECT storage_path, audio_format
            FROM data.bulletin_audio
            WHERE bulletin_id = :id
            ORDER BY id DESC LIMIT 1
        """), {"id": bulletin_id}).mappings().first()

    if row is None:
        raise HTTPException(status_code=404, detail=f"No audio for bulletin {bulletin_id}")

    path = Path(row["storage_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Audio file missing on disk: {row['storage_path']}")

    return FileResponse(str(path), media_type="audio/mpeg", filename=path.name)
