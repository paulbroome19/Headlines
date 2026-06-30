from __future__ import annotations

import json
import logging
import time
from typing import Any

from redis import Redis
from redis.exceptions import ResponseError
from sqlalchemy.orm import Session

from core.platform.queue.processed import ProcessedRepo
from core.platform.queue.registry import HandlerRegistry

logger = logging.getLogger(__name__)


class StreamConsumer:
    """
    Redis Streams consumer (durable via Consumer Groups).

    What this does:
    - Uses XREADGROUP so consumption is restart-safe
    - Routes events by payload["type"] via HandlerRegistry
    - Enforces logical exactly-once via ProcessedRepo (processed_event table)
    - ACKs messages only after successful handler execution + DB commit

    Notes:
    - If a handler raises, we DO NOT ACK (message remains pending in the group)
    - If the message is malformed or has no handlers, we ACK to avoid clogging the stream
    - Optional: can reclaim stale pending messages (for crashed consumers) via claim_stale()
    """

    def __init__(
        self,
        *,
        redis: Redis,
        stream_name: str,
        group: str,
        consumer: str,
        registry: HandlerRegistry,
        reclaim_stale: bool = False,
        reclaim_min_idle_ms: int = 60_000,
    ) -> None:
        self.redis = redis
        self.stream_name = stream_name
        self.group = group
        self.consumer = consumer
        self.registry = registry

        self.reclaim_stale = reclaim_stale
        self.reclaim_min_idle_ms = reclaim_min_idle_ms

    # ----------------------------
    # Public API
    # ----------------------------
    def run_forever(self, db_factory) -> None:
        self._ensure_group()
        print(f"consumer: started (stream={self.stream_name}, group={self.group}, consumer={self.consumer})")

        while True:
            # 1) Read new messages for this consumer group
            messages = self._read_new(count=50, block_ms=2000)

            # 2) Optionally reclaim stale pending messages (useful if consumer crashes)
            if self.reclaim_stale:
                messages.extend(self._claim_stale(min_idle_ms=self.reclaim_min_idle_ms, count=20))

            if not messages:
                continue

            for msg_id, fields in messages:
                # A handler (or DB) failure must NEVER kill the consumer thread.
                # Previously _handle_message re-raised on error and the exception
                # propagated straight out of run_forever, killing the thread and
                # silently stopping ALL event processing forever. Catch here, log,
                # and continue: the failed message stays unacked (pending) for
                # retry/reclaim, while the consumer lives to process the next one.
                try:
                    self._handle_message(msg_id, fields, db_factory)
                except Exception:
                    logger.exception(
                        "consumer: error handling message %s — surviving, message left pending",
                        msg_id,
                    )

            # tiny sleep to reduce tight-loop CPU
            time.sleep(0.01)

    # ----------------------------
    # Group setup / read / ack
    # ----------------------------
    def _ensure_group(self) -> None:
        """
        Create consumer group if it doesn't exist.
        MKSTREAM creates the stream if missing.
        """
        try:
            self.redis.xgroup_create(
                name=self.stream_name,
                groupname=self.group,
                id="0-0",
                mkstream=True,
            )
        except ResponseError as e:
            # BUSYGROUP -> already exists
            if "BUSYGROUP" not in str(e):
                raise

    def _read_new(self, *, count: int, block_ms: int) -> list[tuple[str, dict[str, str]]]:
        """
        Read new messages (">") for this consumer group.
        Returns list of (message_id, fields).
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

    def _ack(self, msg_id: str) -> None:
        self.redis.xack(self.stream_name, self.group, msg_id)

    # ----------------------------
    # Optional: reclaim pending (crash recovery)
    # ----------------------------
    def _claim_stale(self, *, min_idle_ms: int, count: int) -> list[tuple[str, dict[str, str]]]:
        """
        Reclaim messages that are pending (delivered to a consumer) but unacked for a while.
        This makes restarts + multi-consumer setups robust.

        Strategy:
        - Check pending range
        - Select messages idle >= min_idle_ms
        - XCLAIM them to this consumer
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

        stale_ids: list[str] = []
        for p in pending:
            # p looks like: {"message_id": "...", "consumer": "...", "time_since_delivered": 1234, "times_delivered": 1}
            if p.get("time_since_delivered", 0) >= min_idle_ms:
                stale_ids.append(p["message_id"])

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

    # ----------------------------
    # Handling
    # ----------------------------
    def _handle_message(self, msg_id: str, fields: dict[str, str], db_factory) -> None:
        raw = fields.get("json")

        # Poison pill protection: malformed events should not block the stream
        if not raw:
            self._ack(msg_id)
            return

        try:
            payload: dict[str, Any] = json.loads(raw)
        except Exception:
            self._ack(msg_id)
            return

        event_type = payload.get("type")
        idempotency_key = payload.get("idempotency_key")

        # If key fields missing, ack to avoid clogging the stream
        if not event_type or not idempotency_key:
            self._ack(msg_id)
            return

        handlers = self.registry.handlers_for(event_type)

        # If no handlers registered, ack (otherwise the stream backs up forever)
        if not handlers:
            self._ack(msg_id)
            return

        with db_factory() as db:  # Session
            processed = ProcessedRepo(db)

            # If already processed, ack and move on
            if processed.is_processed(idempotency_key):
                self._ack(msg_id)
                return

            try:
                # Run handlers
                for fn in handlers:
                    fn(payload)

                # Mark processed + commit BEFORE ack
                processed.mark_processed(idempotency_key=idempotency_key, event_type=event_type)
                db.commit()

                # Ack only after commit so we don't lose work
                self._ack(msg_id)

            except Exception:
                db.rollback()
                # Do NOT ack: message remains pending for retry/reclaim
                raise