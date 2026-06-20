"""Tests for handle_ingest_requested — focuses on the gnews_enabled gate."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def mock_db_context():
    """Return a mock Session context-manager."""
    db = MagicMock()
    db.__enter__ = MagicMock(return_value=db)
    db.__exit__ = MagicMock(return_value=False)
    return db


@pytest.fixture()
def fake_run():
    return SimpleNamespace(id=42)


def _build_event(payload=None):
    return {"type": "data.ingest.requested", "payload": payload or {}, "trace_id": "t1"}


def test_gnews_disabled_skips_fetch(mock_db_context, fake_run, capsys):
    """When gnews_enabled=False, GNewsClient must never be called and the handler
    must still create the run, enqueue the normalise event with inserted_count=0,
    and commit — no crash."""
    mock_run_repo = MagicMock()
    mock_run_repo.create.return_value = fake_run

    mock_ingested_repo = MagicMock()
    mock_outbox_repo = MagicMock()

    with (
        patch(
            "core.pipeline.data.ingest.handlers.requested.settings"
        ) as mock_settings,
        patch(
            "core.pipeline.data.ingest.handlers.requested.SessionLocal",
            return_value=mock_db_context,
        ),
        patch(
            "core.pipeline.data.ingest.handlers.requested.IngestionRunRepo",
            return_value=mock_run_repo,
        ),
        patch(
            "core.pipeline.data.ingest.handlers.requested.IngestedArticleRepo",
            return_value=mock_ingested_repo,
        ),
        patch(
            "core.pipeline.data.ingest.handlers.requested.OutboxRepo",
            return_value=mock_outbox_repo,
        ),
        patch(
            "core.pipeline.data.ingest.handlers.requested.GNewsClient"
        ) as mock_gnews_cls,
    ):
        mock_settings.gnews_enabled = False

        from core.pipeline.data.ingest.handlers.requested import handle_ingest_requested

        handle_ingest_requested(_build_event())

    # GNewsClient must never have been instantiated
    mock_gnews_cls.assert_not_called()

    # Run must have been created
    mock_run_repo.create.assert_called_once()

    # Normalise event must have been enqueued with inserted_count=0
    mock_outbox_repo.add_event.assert_called_once()
    enqueued_event = mock_outbox_repo.add_event.call_args[0][0]
    assert enqueued_event.payload["inserted_count"] == 0
    assert enqueued_event.payload["ingestion_run_id"] == fake_run.id

    # DB must have been committed
    mock_db_context.commit.assert_called_once()

    # Clear skip message must have been logged
    captured = capsys.readouterr()
    assert "GNEWS_ENABLED=false" in captured.out


def test_gnews_enabled_calls_client(mock_db_context, fake_run):
    """When gnews_enabled=True, GNewsClient is constructed and top_headlines called."""
    mock_run_repo = MagicMock()
    mock_run_repo.create.return_value = fake_run

    mock_ingested_repo = MagicMock()
    mock_ingested_repo.exists_by_dedup_hash.return_value = False
    mock_ingested_repo.create.return_value = MagicMock()
    mock_outbox_repo = MagicMock()

    mock_client = MagicMock()
    mock_client.top_headlines.return_value = []

    with (
        patch(
            "core.pipeline.data.ingest.handlers.requested.settings"
        ) as mock_settings,
        patch(
            "core.pipeline.data.ingest.handlers.requested.SessionLocal",
            return_value=mock_db_context,
        ),
        patch(
            "core.pipeline.data.ingest.handlers.requested.IngestionRunRepo",
            return_value=mock_run_repo,
        ),
        patch(
            "core.pipeline.data.ingest.handlers.requested.IngestedArticleRepo",
            return_value=mock_ingested_repo,
        ),
        patch(
            "core.pipeline.data.ingest.handlers.requested.OutboxRepo",
            return_value=mock_outbox_repo,
        ),
        patch(
            "core.pipeline.data.ingest.handlers.requested.GNewsClient",
            return_value=mock_client,
        ) as mock_gnews_cls,
    ):
        mock_settings.gnews_enabled = True

        from core.pipeline.data.ingest.handlers.requested import handle_ingest_requested

        handle_ingest_requested(_build_event())

    # GNewsClient must have been instantiated and called
    mock_gnews_cls.assert_called_once()
    mock_client.top_headlines.assert_called_once()
