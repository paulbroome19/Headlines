from __future__ import annotations

import json
from typing import Any

from redis import Redis
from redis.exceptions import ResponseError

from core.platform.config.settings import settings
from core.platform.queue.transport.base import Publisher, ConsumerTransport


class RedisStreamsPublisher(Publisher):
    def __init__(self, redis: Redis, stream_name: str) -> None:
        self.redis = redis
        self.stream_name = stream_name

    def publish(self, payload: dict[str, Any]) -> str:
        # Store as single JSON field
        return self.redis.xadd(self.stream_name, {"json": json.dumps(payload)})


class RedisStreamsConsumer(ConsumerTransport):
    def __init__(self, redis: Redis, stream_name: str, group: str, consumer: str) -> None:
        self.redis = redis
        self.stream_name = stream_name
        self.group = group
        self.consumer = consumer

    def ensure_consumer_group(self) -> None:
        """
        Create group if missing. Uses MKSTREAM so the stream is created too.
        """
        try:
            self.redis.xgroup_create(
                name=self.stream_name,
                groupname=self.group,
                id="0-0",
                mkstream=True,
            )
        except ResponseError as e:
            # BUSYGROUP means it already exists
            if "BUSYGROUP" not in str(e):
                raise

    def read(self, count: int = 50, block_ms: int = 2000) -> list[tuple[str, dict[str, str]]]:
        """
        Reads new messages for this group/consumer.
        """
        resp = self.redis.xreadgroup(
            groupname=self.group,
            consumername=self.consumer,
            streams={self.stream_name: ">"},
            count=count,
            block=block_ms,
        )

        messages: list[tuple[str, dict[str, str]]] = []
        if not resp:
            return messages

        # resp: [(stream, [(id, {field: value}), ...])]
        for _stream, items in resp:
            for msg_id, fields in items:
                messages.append((msg_id, fields))
        return messages

    def ack(self, message_id: str) -> None:
        self.redis.xack(self.stream_name, self.group, message_id)

    def pending_summary(self) -> dict[str, Any]:
        """
        Useful for ops/debug: how many pending messages.
        """
        try:
            return self.redis.xpending(self.stream_name, self.group)
        except ResponseError:
            return {"count": 0}

    def claim_stale(
        self,
        *,
        min_idle_ms: int = 60_000,
        count: int = 50,
    ) -> list[tuple[str, dict[str, str]]]:
        """
        Optional: reclaim messages that were delivered but not acked (consumer crashed).
        This makes restarts and multi-consumer setups robust.

        Strategy:
        - Look at pending entries (PEL)
        - Claim those idle longer than min_idle_ms
        """
        try:
            pending = self.redis.xpending_range(
                name=self.stream_name,
                groupname=self.group,
                min="-",
                max="+",
                count=count,
            )
        except ResponseError:
            return []

        stale_ids = [p["message_id"] for p in pending if p.get("time_since_delivered", 0) >= min_idle_ms]
        if not stale_ids:
            return []

        claimed = self.redis.xclaim(
            name=self.stream_name,
            groupname=self.group,
            consumername=self.consumer,
            min_idle_time=min_idle_ms,
            message_ids=stale_ids,
        )

        messages: list[tuple[str, dict[str, str]]] = []
        for msg_id, fields in claimed:
            messages.append((msg_id, fields))
        return messages