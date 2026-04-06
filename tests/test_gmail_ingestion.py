"""Tests for the Gmail ingester."""

import base64
import json
from unittest.mock import AsyncMock, patch

import pytest

from deadline_agent.ingestion.gmail import GmailIngester
from deadline_agent.pipeline import pipeline_queue


def _mock_token_manager() -> AsyncMock:
    return AsyncMock(get_valid_token=AsyncMock(return_value="test-token"))


def _pubsub_payload(email: str = "user@gmail.com", history_id: str = "12345") -> dict:  # type: ignore[type-arg]
    data = json.dumps({"emailAddress": email, "historyId": history_id})
    encoded = base64.b64encode(data.encode()).decode()
    return {
        "message": {
            "data": encoded,
            "messageId": "msg-001",
            "publishTime": "2026-03-23T10:00:00Z",
        },
        "subscription": "projects/test/subscriptions/gmail-push",
    }


class TestValidatePayload:
    @pytest.mark.asyncio
    async def test_valid_payload(self) -> None:
        ingester = GmailIngester(token_manager=_mock_token_manager())
        assert await ingester.validate_payload(_pubsub_payload())

    @pytest.mark.asyncio
    async def test_missing_data(self) -> None:
        ingester = GmailIngester(token_manager=_mock_token_manager())
        assert not await ingester.validate_payload({"message": {"messageId": "1"}})

    @pytest.mark.asyncio
    async def test_missing_message(self) -> None:
        ingester = GmailIngester(token_manager=_mock_token_manager())
        assert not await ingester.validate_payload({"subscription": "test"})


class TestHandleWebhook:
    @pytest.mark.asyncio
    async def test_enqueues_items(self) -> None:
        ingester = GmailIngester(token_manager=_mock_token_manager())

        mock_messages = [
            {"id": "msg1", "snippet": "Submit HW3", "subject": "HW3 Due", "from": "prof@mit.edu"},
        ]
        with patch.object(ingester, "_fetch_messages_since", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_messages

            # Drain the queue first
            while not pipeline_queue.empty():
                pipeline_queue.get_nowait()

            await ingester.handle_webhook(_pubsub_payload())

            assert not pipeline_queue.empty()
            item = pipeline_queue.get_nowait()
            assert item.source == "gmail"
            assert item.raw_content == "Submit HW3"
            assert item.metadata["subject"] == "HW3 Due"

    @pytest.mark.asyncio
    async def test_no_history_id(self) -> None:
        ingester = GmailIngester(token_manager=_mock_token_manager())
        data = json.dumps({"emailAddress": "user@gmail.com"})
        encoded = base64.b64encode(data.encode()).decode()
        payload = {"message": {"data": encoded, "messageId": "1"}}

        with patch.object(ingester, "_fetch_messages_since", new_callable=AsyncMock) as mock_fetch:
            await ingester.handle_webhook(payload)
            mock_fetch.assert_not_called()
