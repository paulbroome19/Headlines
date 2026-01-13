from __future__ import annotations

import time

from redis import Redis

from core.platform.config.settings import settings
from core.platform.db.session import SessionLocal
from core.platform.queue.outbox import OutboxRepo
from core.platform.queue.transport.redis import RedisStreamsPublisher


def main() -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    publisher = RedisStreamsPublisher(redis, settings.redis_stream_events)

    print("dispatcher: started")

    while True:
        with SessionLocal() as db:
            repo = OutboxRepo(db)
            rows = repo.fetch_pending(limit=50)

            if not rows:
                time.sleep(0.5)
                continue

            published_ids: list[str] = []
            for row in rows:
                try:
                    publisher.publish(row.payload)
                    published_ids.append(str(row.id))
                except Exception as e:
                    repo.mark_failed(str(row.id), str(e))

            if published_ids:
                repo.mark_published(published_ids)

            db.commit()


if __name__ == "__main__":
    main()