from typing import Optional

from fastapi import APIRouter
from sqlalchemy import text

from core.platform.db.session import SessionLocal

router = APIRouter(prefix="/audio/outputs", tags=["audio"])


@router.get("/latest")
def latest_audio(feed_id: Optional[int] = None) -> dict:
    """
    Latest audio output.

    - If feed_id is provided: latest audio output for that feed
    - If feed_id is omitted: latest audio output overall
    """
    with SessionLocal() as db:
        if feed_id is None:
            row = db.execute(
                text(
                    """
                    SELECT id, feed_id, scripts_output_id, variant, status, format, storage_path, duration_seconds, created_at
                    FROM audio.outputs
                    ORDER BY id DESC
                    LIMIT 1
                    """
                )
            ).first()
        else:
            row = db.execute(
                text(
                    """
                    SELECT id, feed_id, scripts_output_id, variant, status, format, storage_path, duration_seconds, created_at
                    FROM audio.outputs
                    WHERE feed_id = :feed_id
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ),
                {"feed_id": int(feed_id)},
            ).first()

        if not row:
            return {"detail": "No audio outputs found"}

        return {
            "id": row.id,
            "feed_id": row.feed_id,
            "scripts_output_id": row.scripts_output_id,
            "variant": row.variant,
            "status": row.status,
            "format": row.format,
            "storage_path": row.storage_path,
            "duration_seconds": row.duration_seconds,
            "created_at": row.created_at.isoformat().replace("+00:00", "Z") if row.created_at else None,
        }