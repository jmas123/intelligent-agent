"""Tests for the Ollama extractor."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from deadline_agent.extraction.extractor import ExtractionError, OllamaExtractor
from deadline_agent.pipeline import IngestItem


def _sample_item() -> IngestItem:
    return IngestItem(
        source="gmail",
        raw_content="Please submit HW3 by March 25",
        metadata={
            "subject": "HW3 Due Friday",
            "sender": "prof@mit.edu",
            "raw_hash": "abc123",
        },
    )


def _mock_response(data: dict) -> AsyncMock:  # type: ignore[type-arg]
    """Create a mock Ollama response."""
    mock = AsyncMock()
    mock.message.content = json.dumps(data)
    return mock


VALID_EXTRACTION = {
    "title": "Submit HW3",
    "due_date_iso": "2026-03-25T23:59:00",
    "source": "gmail",
    "type": "assignment",
    "course": "CS 101",
    "urgency_score": 4,
    "confidence": 0.92,
    "raw_hash": "abc123",
}


@pytest.mark.asyncio
async def test_successful_extraction() -> None:
    extractor = OllamaExtractor()
    with patch.object(extractor, "_client") as mock_client:
        mock_client.chat = AsyncMock(return_value=_mock_response(VALID_EXTRACTION))
        result = await extractor.extract(_sample_item())

    assert result is not None
    assert result.title == "Submit HW3"
    assert result.source == "gmail"
    assert result.raw_hash == "abc123"


@pytest.mark.asyncio
async def test_low_confidence_discarded() -> None:
    extractor = OllamaExtractor()
    low_conf = {**VALID_EXTRACTION, "confidence": 0.4}
    with patch.object(extractor, "_client") as mock_client:
        mock_client.chat = AsyncMock(return_value=_mock_response(low_conf))
        result = await extractor.extract(_sample_item())

    assert result is None


@pytest.mark.asyncio
async def test_medium_confidence_returned_with_warning() -> None:
    extractor = OllamaExtractor()
    med_conf = {**VALID_EXTRACTION, "confidence": 0.65}
    with patch.object(extractor, "_client") as mock_client:
        mock_client.chat = AsyncMock(return_value=_mock_response(med_conf))
        result = await extractor.extract(_sample_item())

    assert result is not None
    assert result.confidence == 0.65


@pytest.mark.asyncio
async def test_source_overridden_from_metadata() -> None:
    extractor = OllamaExtractor()
    wrong_source = {**VALID_EXTRACTION, "source": "moodle", "raw_hash": "wrong"}
    with patch.object(extractor, "_client") as mock_client:
        mock_client.chat = AsyncMock(return_value=_mock_response(wrong_source))
        result = await extractor.extract(_sample_item())

    assert result is not None
    assert result.source == "gmail"
    assert result.raw_hash == "abc123"


@pytest.mark.asyncio
async def test_ollama_connection_failure_raises() -> None:
    extractor = OllamaExtractor()
    with patch.object(extractor, "_client") as mock_client:
        mock_client.chat = AsyncMock(side_effect=ConnectionError("Ollama down"))
        with pytest.raises(ExtractionError):
            await extractor.extract(_sample_item())


@pytest.mark.asyncio
async def test_invalid_json_response() -> None:
    extractor = OllamaExtractor()
    mock_resp = AsyncMock()
    mock_resp.message.content = "not valid json"
    with patch.object(extractor, "_client") as mock_client:
        mock_client.chat = AsyncMock(return_value=mock_resp)
        result = await extractor.extract(_sample_item())

    assert result is None


@pytest.mark.asyncio
async def test_build_prompt() -> None:
    extractor = OllamaExtractor()
    item = _sample_item()
    prompt = extractor._build_prompt(item)
    assert "Subject: HW3 Due Friday" in prompt
    assert "From: prof@mit.edu" in prompt
    assert "Please submit HW3 by March 25" in prompt
