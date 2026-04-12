from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo


DATA_INGEST_REQUESTED = "data.ingest.requested"


@dataclass(slots=True)
class StageSnapshot:
    stage_name: str
    latest_created_at: datetime | None
    latest_published_at: datetime | None
    recent_count_6h: int
    recent_count_24h: int


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def format_dt(value: datetime | None) -> str:
    if value is None:
        return "None"
    return value.isoformat()


def get_stage_snapshot(
    *,
    session,
    table_name: str,
    stage_name: str,
    has_published_at: bool,
) -> StageSnapshot:
    latest_created_sql = text(
        f"""
        SELECT MAX(created_at) AS latest_created_at
        FROM {table_name}
        """
    )
    latest_created_row = session.execute(latest_created_sql).mappings().one()
    latest_created_at = ensure_utc(latest_created_row["latest_created_at"])

    latest_published_at = None
    if has_published_at:
        latest_published_sql = text(
            f"""
            SELECT MAX(published_at) AS latest_published_at
            FROM {table_name}
            """
        )
        latest_published_row = session.execute(latest_published_sql).mappings().one()
        latest_published_at = ensure_utc(latest_published_row["latest_published_at"])

    recent_count_6h_sql = text(
        f"""
        SELECT COUNT(*) AS count
        FROM {table_name}
        WHERE created_at >= (NOW() AT TIME ZONE 'UTC') - INTERVAL '6 hours'
        """
    )
    recent_count_24h_sql = text(
        f"""
        SELECT COUNT(*) AS count
        FROM {table_name}
        WHERE created_at >= (NOW() AT TIME ZONE 'UTC') - INTERVAL '24 hours'
        """
    )

    recent_count_6h = int(session.execute(recent_count_6h_sql).scalar_one())
    recent_count_24h = int(session.execute(recent_count_24h_sql).scalar_one())

    return StageSnapshot(
        stage_name=stage_name,
        latest_created_at=latest_created_at,
        latest_published_at=latest_published_at,
        recent_count_6h=recent_count_6h,
        recent_count_24h=recent_count_24h,
    )


def get_latest_clustered_article(session) -> dict[str, Any] | None:
    sql = text(
        """
        SELECT
            id,
            title,
            source,
            published_at,
            category_primary,
            story_id,
            created_at
        FROM data.normalisation_articles
        WHERE story_id IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 1
        """
    )
    row = session.execute(sql).mappings().first()
    if row is None:
        return None

    result = dict(row)
    result["published_at"] = ensure_utc(result.get("published_at"))
    result["created_at"] = ensure_utc(result.get("created_at"))
    return result


def get_recent_clustered_counts(session) -> dict[str, int]:
    sql = text(
        """
        SELECT
            COUNT(*) FILTER (
                WHERE created_at >= (NOW() AT TIME ZONE 'UTC') - INTERVAL '6 hours'
            ) AS clustered_6h,
            COUNT(*) FILTER (
                WHERE created_at >= (NOW() AT TIME ZONE 'UTC') - INTERVAL '24 hours'
            ) AS clustered_24h
        FROM data.normalisation_articles
        WHERE story_id IS NOT NULL
        """
    )
    row = session.execute(sql).mappings().one()
    return {
        "clustered_6h": int(row["clustered_6h"] or 0),
        "clustered_24h": int(row["clustered_24h"] or 0),
    }


def print_stage_snapshot(snapshot: StageSnapshot) -> None:
    print(f"{snapshot.stage_name}")
    print(f"  latest_created_at : {format_dt(snapshot.latest_created_at)}")
    print(f"  latest_published_at: {format_dt(snapshot.latest_published_at)}")
    print(f"  rows_last_6h      : {snapshot.recent_count_6h}")
    print(f"  rows_last_24h     : {snapshot.recent_count_24h}")
    print()


def print_health() -> None:
    with SessionLocal() as session:
        ingested = get_stage_snapshot(
            session=session,
            table_name="data.ingested_articles",
            stage_name="INGESTED ARTICLES",
            has_published_at=True,
        )
        normalised = get_stage_snapshot(
            session=session,
            table_name="data.normalisation_articles",
            stage_name="NORMALISED ARTICLES",
            has_published_at=True,
        )
        clustered_counts = get_recent_clustered_counts(session)
        latest_clustered = get_latest_clustered_article(session)

    print_stage_snapshot(ingested)
    print_stage_snapshot(normalised)

    print("CLUSTERED OUTPUT")
    print(f"  clustered_rows_last_6h  : {clustered_counts['clustered_6h']}")
    print(f"  clustered_rows_last_24h : {clustered_counts['clustered_24h']}")
    if latest_clustered is None:
        print("  latest_clustered_article: None")
    else:
        print("  latest_clustered_article:")
        print(f"    id              : {latest_clustered['id']}")
        print(f"    title           : {latest_clustered['title']}")
        print(f"    source          : {latest_clustered['source']}")
        print(f"    published_at    : {format_dt(latest_clustered['published_at'])}")
        print(f"    category_primary: {latest_clustered['category_primary']}")
        print(f"    story_id        : {latest_clustered['story_id']}")
        print(f"    created_at      : {format_dt(latest_clustered['created_at'])}")
    print()


def publish_ingest_request() -> tuple[str | None, str]:
    idempotency_key = f"ingest:{uuid.uuid4()}"

    event = Event(
        type=DATA_INGEST_REQUESTED,
        idempotency_key=idempotency_key,
        payload={
            "source": "manual-refresh",
            "provider": "gnews",
            "country": "gb",
            "lang": "en",
            "max_results": 25,
        },
        trace_id=None,
    )

    with SessionLocal() as session:
        outbox = OutboxRepo(session)
        outbox.add_event(event)
        session.commit()

    event_id = getattr(event, "event_id", None) or getattr(event, "id", None)
    return event_id, idempotency_key


def main() -> None:
    now = datetime.now(timezone.utc)
    print(f"\nManual pipeline refresh at {now.isoformat()}\n")

    print("Health before refresh:\n")
    print_health()

    event_id, idempotency_key = publish_ingest_request()

    print("Published event:")
    print(f"  type            : {DATA_INGEST_REQUESTED}")
    print(f"  event_id        : {event_id}")
    print(f"  idempotency_key : {idempotency_key}")
    print()

    print("Waiting 5 seconds before checking health again...")
    time.sleep(5)

    print("\nHealth after refresh attempt:\n")
    print_health()

    print("Reminder:")
    print("  Make sure the consumer is running in another terminal:")
    print("  poetry run python -m core.workers.run_consumer")
    print()


if __name__ == "__main__":
    main()