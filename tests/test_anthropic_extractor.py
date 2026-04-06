"""Tests for the Anthropic extractor."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deadline_agent.extraction.anthropic_extractor import AnthropicExtractor
from deadline_agent.extraction.extractor import ExtractionError
from deadline_agent.pipeline import IngestItem

VALID_TOOL_INPUT = {
    "title": "Submit HW3",
    "due_date_iso": "2026-03-25T23:59:00",
    "source": "gmail",
    "type": "assignment",
    "course": "CS 101",
    "urgency_score": 4,
    "confidence": 0.92,
    "raw_hash": "abc123",
}


def _sample_item() -> IngestItem:
    return IngestItem(
        source="gmail",
        raw_content="Please submit HW3 by March 25",
        metadata={"subject": "HW3 Due", "sender": "prof@mit.edu", "raw_hash": "abc123"},
    )


def _mock_tool_response(tool_input: dict) -> MagicMock:  # type: ignore[type-arg]
    """Create a mock Anthropic response with tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = "extract_task"
    block.input = tool_input
    response = MagicMock()
    response.content = [block]
    return response


@pytest.mark.asyncio
async def test_successful_extraction() -> None:
    extractor = AnthropicExtractor(api_key="test-key")
    with patch.object(extractor, "_client") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=_mock_tool_response(VALID_TOOL_INPUT))
        result = await extractor.extract(_sample_item())

    assert result is not None
    assert result.title == "Submit HW3"
    assert result.source == "gmail"


@pytest.mark.asyncio
async def test_low_confidence_discarded() -> None:
    extractor = AnthropicExtractor(api_key="test-key")
    low_conf = {**VALID_TOOL_INPUT, "confidence": 0.4}
    with patch.object(extractor, "_client") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=_mock_tool_response(low_conf))
        result = await extractor.extract(_sample_item())

    assert result is None


@pytest.mark.asyncio
async def test_source_overridden() -> None:
    extractor = AnthropicExtractor(api_key="test-key")
    wrong = {**VALID_TOOL_INPUT, "source": "canvas", "raw_hash": "wrong"}
    with patch.object(extractor, "_client") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=_mock_tool_response(wrong))
        result = await extractor.extract(_sample_item())

    assert result is not None
    assert result.source == "gmail"
    assert result.raw_hash == "abc123"


@pytest.mark.asyncio
async def test_api_error_raises_extraction_error() -> None:
    extractor = AnthropicExtractor(api_key="test-key")
    with patch.object(extractor, "_client") as mock_client:
        mock_client.messages.create = AsyncMock(side_effect=Exception("API error"))
        with pytest.raises(ExtractionError):
            await extractor.extract(_sample_item())


@pytest.mark.asyncio
async def test_no_tool_use_block() -> None:
    extractor = AnthropicExtractor(api_key="test-key")
    response = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    response.content = [text_block]
    with patch.object(extractor, "_client") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=response)
        result = await extractor.extract(_sample_item())

    assert result is None


def test_no_api_key_raises() -> None:
    with patch("deadline_agent.extraction.anthropic_extractor.settings") as mock_settings:
        mock_settings.anthropic_api_key = None
        mock_settings.anthropic_extraction_model = "claude-haiku-4-5-20251001"
        with pytest.raises(ValueError, match="API key is required"):
            AnthropicExtractor()
