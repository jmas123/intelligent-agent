"""Tests for Gmail draft creation."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deadline_agent.actions.gmail import create_gmail_draft

SAMPLE_PAYLOAD = {
    "to": "ta@university.edu",
    "subject": "Re: HW3 Question",
    "body": "Hi, I have a question about the deadline.",
}


@pytest.mark.asyncio
async def test_create_draft_success() -> None:
    mock_tm = AsyncMock()
    mock_tm.get_valid_token.return_value = "fake-token"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "draft123"}

    with patch("deadline_agent.actions.gmail.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await create_gmail_draft(SAMPLE_PAYLOAD, token_manager=mock_tm)

    assert result is not None
    assert result["id"] == "draft123"


@pytest.mark.asyncio
async def test_create_draft_no_token() -> None:
    mock_tm = AsyncMock()
    mock_tm.get_valid_token.return_value = None

    result = await create_gmail_draft(SAMPLE_PAYLOAD, token_manager=mock_tm)
    assert result is None


@pytest.mark.asyncio
async def test_create_draft_api_error() -> None:
    mock_tm = AsyncMock()
    mock_tm.get_valid_token.return_value = "fake-token"

    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.text = "Forbidden"

    with patch("deadline_agent.actions.gmail.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await create_gmail_draft(SAMPLE_PAYLOAD, token_manager=mock_tm)

    assert result is None
