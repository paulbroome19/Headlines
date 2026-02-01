from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from starlette.responses import FileResponse

from core.platform.db.session import SessionLocal

router = APIRouter(prefix="/audio/outputs", tags=["audio"])


@router.get("/latest")
def latest_audio_output(feed_id: int) -> dict:
    """
    Returns the latest audio output row for a given feed_id.
    """
    with SessionLocal() as db:
        row = db.execute(
            text(
                """
                SELECT
                  id,
                  feed_id,
                  scripts_output_id,
                  variant,
                  status,
                  format,
                  storage_path,
                  duration_seconds,
                  created_at
                FROM audio.outputs
                WHERE feed_id = :feed_id
                ORDER BY id DESC
                LIMIT 1
                """
            ),
            {"feed_id": int(feed_id)},
        ).mappings().first()

        if not row:
            raise HTTPException(status_code=404, detail="No audio outputs found")

        return dict(row)


@router.get("/{scripts_output_id}/file")
def audio_file(scripts_output_id: int):
    """
    Returns the audio file bytes for a given scripts_output_id.
    """
    with SessionLocal() as db:
        row = db.execute(
            text(
                """
                SELECT storage_path, format
                FROM audio.outputs
                WHERE scripts_output_id = :scripts_output_id
                ORDER BY id DESC
                LIMIT 1
                """
            ),
            {"scripts_output_id": int(scripts_output_id)},
        ).mappings().first()

        if not row:
            raise HTTPException(status_code=404, detail="Audio output not found for scripts_output_id")

        storage_path = row["storage_path"]
        fmt = (row.get("format") or "wav").lower()

        if not storage_path:
            raise HTTPException(status_code=404, detail="Audio output has no storage_path")

        path = Path(storage_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"Audio file missing on disk: {storage_path}")

        media_type = "audio/wav" if fmt == "wav" else "application/octet-stream"

        # Give the downloaded file a friendly name
        filename = path.name

        return FileResponse(
            path=str(path),
            media_type=media_type,
            filename=filename,
        )