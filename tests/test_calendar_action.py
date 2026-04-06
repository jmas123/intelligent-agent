"""Tests for Google Calendar event creation."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deadline_agent.actions.calendar import create_calendar_event

SAMPLE_PAYLOAD = {
    "summary": "Work on HW3",
    "start_iso": "2026-03-25T09:00:00",
    "end_iso": "2026-03-25T11:00:00",
}


@pytest.mark.asyncio
async def test_create_event_success() -> None:
    mock_tm = AsyncMock()
    mock_tm.get_valid_token.return_value = "fake-token"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "event123", "summary": "Work on HW3"}

    with patch("deadline_agent.actions.calendar.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await create_calendar_event(SAMPLE_PAYLOAD, token_manager=mock_tm)

    assert result is not None
    assert result["id"] == "event123"


@pytest.mark.asyncio
async def test_create_event_no_token() -> None:
    mock_tm = AsyncMock()
    mock_tm.get_valid_token.return_value = None

    result = await create_calendar_event(SAMPLE_PAYLOAD, token_manager=mock_tm)
    assert result is None


@pytest.mark.asyncio
async def test_create_event_api_error() -> None:
    mock_tm = AsyncMock()
    mock_tm.get_valid_token.return_value = "fake-token"

    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.text = "Forbidden"

    with patch("deadline_agent.actions.calendar.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await create_calendar_event(SAMPLE_PAYLOAD, token_manager=mock_tm)

    assert result is None


@pytest.mark.asyncio
async def test_create_event_with_description() -> None:
    mock_tm = AsyncMock()
    mock_tm.get_valid_token.return_value = "fake-token"

    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {"id": "event456"}

    with patch("deadline_agent.actions.calendar.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        payload = {**SAMPLE_PAYLOAD, "description": "Focus time for CS 101"}
        result = await create_calendar_event(payload, token_manager=mock_tm)

    assert result is not None
    # Verify description was included in the POST body
    call_kwargs = mock_client.post.call_args
    assert "description" in call_kwargs.kwargs["json"]
