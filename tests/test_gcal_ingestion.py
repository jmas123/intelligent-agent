"""Tests for the Google Calendar ingester."""

import base64
import json
from unittest.mock import AsyncMock, patch

import pytest

from deadline_agent.ingestion.gcal import GCalIngester
from deadline_agent.pipeline import pipeline_queue


def _pubsub_payload(calendar_id: str = "primary") -> dict:  # type: ignore[type-arg]
    data = json.dumps({"calendarId": calendar_id})
    encoded = base64.b64encode(data.encode()).decode()
    return {
        "message": {
            "data": encoded,
            "messageId": "msg-001",
            "publishTime": "2026-03-23T10:00:00Z",
        },
        "subscription": "projects/test/subscriptions/gcal-push",
    }


def _mock_token_manager() -> AsyncMock:
    tm = AsyncMock()
    tm.get_valid_token.return_value = "test-token"
    return tm


class TestValidatePayload:
    @pytest.mark.asyncio
    async def test_valid_payload(self) -> None:
        ingester = GCalIngester(token_manager=_mock_token_manager())
        assert await ingester.validate_payload(_pubsub_payload())

    @pytest.mark.asyncio
    async def test_missing_data(self) -> None:
        ingester = GCalIngester(token_manager=_mock_token_manager())
        assert not await ingester.validate_payload({"message": {"messageId": "1"}})

    @pytest.mark.asyncio
    async def test_missing_message(self) -> None:
        ingester = GCalIngester(token_manager=_mock_token_manager())
        assert not await ingester.validate_payload({"subscription": "test"})


class TestHandleWebhook:
    @pytest.mark.asyncio
    async def test_enqueues_events(self) -> None:
        ingester = GCalIngester(token_manager=_mock_token_manager())

        mock_events = [
            {
                "id": "event1",
                "summary": "CS 101 Office Hours",
                "description": "Weekly office hours",
                "start": {"dateTime": "2026-03-25T14:00:00Z"},
                "end": {"dateTime": "2026-03-25T15:00:00Z"},
                "organizer": {"email": "prof@university.edu"},
            },
        ]
        with patch.object(ingester, "_fetch_recent_events", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_events

            # Drain queue
            while not pipeline_queue.empty():
                pipeline_queue.get_nowait()

            await ingester.handle_webhook(_pubsub_payload())

            assert not pipeline_queue.empty()
            item = pipeline_queue.get_nowait()
            assert item.source == "gcal"
            assert item.metadata["subject"] == "CS 101 Office Hours"
            assert item.metadata["sender"] == "prof@university.edu"
            assert item.metadata["due_date_iso"] == "2026-03-25T15:00:00Z"

    @pytest.mark.asyncio
    async def test_handles_all_day_event(self) -> None:
        ingester = GCalIngester(token_manager=_mock_token_manager())

        mock_events = [
            {
                "id": "event2",
                "summary": "Midterm",
                "description": "",
                "start": {"date": "2026-04-01"},
                "end": {"date": "2026-04-01"},
                "organizer": {"email": "dept@uni.edu"},
            },
        ]
        with patch.object(ingester, "_fetch_recent_events", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_events

            while not pipeline_queue.empty():
                pipeline_queue.get_nowait()

            await ingester.handle_webhook(_pubsub_payload())

            item = pipeline_queue.get_nowait()
            assert item.metadata["due_date_iso"] == "2026-04-01"
