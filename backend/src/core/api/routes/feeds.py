from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from core.platform.db.session import SessionLocal

router = APIRouter(prefix="/feeds", tags=["feeds"])


@router.get("/latest")
def latest_feed() -> dict:
    """
    Returns the most recent feed and its items.
    """
    with SessionLocal() as db:
        feed = db.execute(
            text("SELECT id, source, created_at FROM feeds.feeds ORDER BY id DESC LIMIT 1")
        ).mappings().first()

        if not feed:
            raise HTTPException(status_code=404, detail="No feeds found")

        items = db.execute(
            text(
                """
                SELECT
                  fi.id,
                  fi.feed_id,
                  fi.summarise_output_id,
                  fi.position,
                  fi.created_at
                FROM feeds.feed_items fi
                WHERE fi.feed_id = :feed_id
                ORDER BY fi.position ASC
                """
            ),
            {"feed_id": feed["id"]},
        ).mappings().all()

        return {"feed": dict(feed), "items": [dict(i) for i in items]}