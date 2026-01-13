from sqlalchemy import text

from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo

from core.pipeline.scripts.events import SCRIPTS_BUILT
from core.pipeline.scripts.repos.output_repo import ScriptOutputRepo


def handle_scripts_requested(event: dict) -> None:
    """
    Handle: scripts.requested

    Minimal v1:
    - Read feed items -> summarise outputs
    - Assemble a single script text blob
    - Write scripts.outputs
    - Emit scripts.built
    """
    payload = event.get("payload") or {}
    trace_id = event.get("trace_id")

    feed_id = payload.get("feed_id")
    if feed_id is None:
        return

    with SessionLocal() as db:
        outbox = OutboxRepo(db)
        outputs = ScriptOutputRepo(db)

        # Pull summarised content in feed order
        rows = db.execute(
            text(
                """
                SELECT fi.position, so.content
                FROM feeds.feed_items fi
                JOIN data.summarise_outputs so
                  ON so.id = fi.summarise_output_id
                WHERE fi.feed_id = :feed_id
                ORDER BY fi.position ASC
                """
            ),
            {"feed_id": int(feed_id)},
        ).all()

        if not rows:
            # Nothing to build; do not mark processed as built
            return

        # Assemble (placeholder formatting for now)
        parts: list[str] = []
        parts.append("Welcome to Headlines.\n")
        for pos, content in rows:
            parts.append(f"{pos}. {content}\n")
        parts.append("\nThat’s your update. Thanks for listening.")

        content = "\n".join(parts)

        out = outputs.create(feed_id=int(feed_id), content=content, variant="default")

        outbox.add_event(
            Event(
                type=SCRIPTS_BUILT,
                idempotency_key=f"scripts:built:{feed_id}",
                payload={"feed_id": int(feed_id), "script_output_id": out.id},
                trace_id=trace_id,
            )
        )

        db.commit()