from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from core.platform.db.session import SessionLocal

router = APIRouter(prefix="/scripts", tags=["scripts"])


@router.get("/outputs/latest")
def latest_script_output(feed_id: int | None = None) -> dict:
    """
    Returns the most recent scripts.outputs row.
    Optionally filter by feed_id.
    """
    with SessionLocal() as db:
        if feed_id is None:
            row = db.execute(
                text(
                    """
                    SELECT id, feed_id, variant, status, content, created_at
                    FROM scripts.outputs
                    ORDER BY id DESC
                    LIMIT 1
                    """
                )
            ).mappings().first()
        else:
            row = db.execute(
                text(
                    """
                    SELECT id, feed_id, variant, status, content, created_at
                    FROM scripts.outputs
                    WHERE feed_id = :feed_id
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ),
                {"feed_id": int(feed_id)},
            ).mappings().first()

        if not row:
            raise HTTPException(status_code=404, detail="No script outputs found")

        return dict(row)


@router.get("/outputs/{output_id}")
def get_script_output(output_id: int) -> dict:
    with SessionLocal() as db:
        row = db.execute(
            text(
                """
                SELECT id, feed_id, variant, status, content, created_at
                FROM scripts.outputs
                WHERE id = :id
                """
            ),
            {"id": int(output_id)},
        ).mappings().first()

        if not row:
            raise HTTPException(status_code=404, detail="Script output not found")

        return dict(row)