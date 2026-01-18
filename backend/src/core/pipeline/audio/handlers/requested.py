from __future__ import annotations

import os
from sqlalchemy import text

from core.platform.config.settings import settings
from core.platform.db.session import SessionLocal
from core.platform.queue.event import Event
from core.platform.queue.outbox import OutboxRepo

from core.pipeline.audio.events import AUDIO_BUILT
from core.pipeline.audio.repos.audio_output_repo import AudioOutputRepo
from core.pipeline.audio.steps.render_stub_wav import estimate_duration_seconds, write_silence_wav


def handle_audio_requested(event: dict) -> None:
    payload = event.get("payload") or {}
    trace_id = event.get("trace_id")

    scripts_output_id = payload.get("scripts_output_id")
    if scripts_output_id is None:
        return

    with SessionLocal() as db:
        so = db.execute(
            text(
                """
                SELECT id, feed_id, variant, content
                FROM scripts.outputs
                WHERE id = :id
                """
            ),
            {"id": int(scripts_output_id)},
        ).mappings().first()

        if not so:
            return

        feed_id = int(so["feed_id"])
        variant = str(so["variant"])
        content = str(so["content"] or "")

        repo = AudioOutputRepo(db)
        if repo.exists_for_scripts_output(int(scripts_output_id)):
            return

        seconds = estimate_duration_seconds(content)

        base_dir = settings.audio_local_dir
        abs_path = os.path.join(os.getcwd(), base_dir, f"feed_{feed_id}_scripts_{int(scripts_output_id)}.wav")
        write_silence_wav(abs_path, seconds)

        audio_output_id = repo.create(
            feed_id=feed_id,
            scripts_output_id=int(scripts_output_id),
            variant=variant,
            status="built",
            format="wav",
            storage_path=abs_path,
            duration_seconds=seconds,
        )

        outbox = OutboxRepo(db)
        outbox.add_event(
            Event(
                type=AUDIO_BUILT,
                idempotency_key=f"audio:built:{int(scripts_output_id)}",
                payload={
                    "audio_output_id": audio_output_id,
                    "feed_id": feed_id,
                    "scripts_output_id": int(scripts_output_id),
                },
                trace_id=trace_id,
            )
        )

        db.commit()