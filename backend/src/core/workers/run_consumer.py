from redis import Redis

from core.platform.config.settings import settings
from core.platform.db.session import SessionLocal
from core.platform.queue.consumer import StreamConsumer
from core.platform.queue.registry_wiring import build_registry


def main() -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    registry = build_registry()

    consumer = StreamConsumer(
        redis=redis,
        stream_name=settings.redis_stream_events,
        group=settings.redis_consumer_group,
        consumer=settings.redis_consumer_name,
        registry=registry,
        reclaim_stale=True,  # turn on later once stable
    )

    consumer.run_forever(db_factory=SessionLocal)


if __name__ == "__main__":
    main()