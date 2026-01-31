from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from core.platform.db.session import SessionLocal

router = APIRouter(prefix="/feeds", tags=["feeds"])


def _iso(dt: datetime) -> str:
    """
    Make sure datetimes are ISO8601 strings with 'Z' for UTC.
    (Keeps iOS decoding simple and consistent.)
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


@router.get("/latest")
def latest_feed() -> dict:
    """
    Returns the most recent feed and its items.
    Includes the summary text (from data.summarise_outputs.content) for each item.
    """
    with SessionLocal() as db:
        feed = db.execute(
            text(
                """
                SELECT id, source, created_at
                FROM feeds.feeds
                ORDER BY id DESC
                LIMIT 1
                """
            )
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
                  fi.created_at,
                  so.content AS text
                FROM feeds.feed_items fi
                JOIN data.summarise_outputs so
                  ON so.id = fi.summarise_output_id
                WHERE fi.feed_id = :feed_id
                ORDER BY fi.position ASC
                """
            ),
            {"feed_id": int(feed["id"])},
        ).mappings().all()

        feed_out = dict(feed)
        feed_out["created_at"] = _iso(feed_out["created_at"])

        items_out = []
        for row in items:
            d = dict(row)
            d["created_at"] = _iso(d["created_at"])
            # Ensure text is always a string (never null)
            d["text"] = d.get("text") or ""
            items_out.append(d)

        return {"feed": feed_out, "items": items_out}