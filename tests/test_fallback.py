"""Tests for the fallback extractor."""

from unittest.mock import AsyncMock, patch

import pytest

from deadline_agent.extraction.extractor import ExtractionError
from deadline_agent.extraction.schemas import ExtractedTask
from deadline_agent.pipeline import IngestItem

VALID_TASK = ExtractedTask(
    title="Submit HW3",
    due_date_iso="2026-03-25T23:59:00",
    source="gmail",
    type="assignment",
    course="CS 101",
    urgency_score=4,
    confidence=0.92,
    raw_hash="abc123",
)


def _sample_item() -> IngestItem:
    return IngestItem(
        source="gmail",
        raw_content="Please submit HW3 by March 25",
        metadata={"subject": "HW3 Due", "sender": "prof@mit.edu", "raw_hash": "abc123"},
    )


@pytest.mark.asyncio
async def test_ollama_succeeds_no_fallback() -> None:
    with patch("deadline_agent.extraction.fallback.settings") as mock_settings:
        mock_settings.use_anthropic_fallback = True
        mock_settings.anthropic_api_key = "test-key"

        from deadline_agent.extraction.fallback import FallbackExtractor

        extractor = FallbackExtractor()

    with patch.object(extractor, "_ollama") as mock_ollama:
        mock_ollama.extract = AsyncMock(return_value=VALID_TASK)
        result = await extractor.extract(_sample_item())

    assert result is not None
    assert result.title == "Submit HW3"


@pytest.mark.asyncio
async def test_ollama_fails_anthropic_succeeds() -> None:
    with patch("deadline_agent.extraction.fallback.settings") as mock_settings:
        mock_settings.use_anthropic_fallback = True
        mock_settings.anthropic_api_key = "test-key"

        from deadline_agent.extraction.fallback import FallbackExtractor

        extractor = FallbackExtractor()

    with (
        patch.object(extractor, "_ollama") as mock_ollama,
        patch(
            "deadline_agent.extraction.anthropic_extractor.AnthropicExtractor",
            create=True,
        ) as mock_anthropic_cls,
    ):
        mock_ollama.extract = AsyncMock(side_effect=ExtractionError("Ollama down"))
        mock_anthropic = AsyncMock()
        mock_anthropic.extract = AsyncMock(return_value=VALID_TASK)
        mock_anthropic_cls.return_value = mock_anthropic

        # Patch the lazy import inside fallback.extract
        with patch(
            "deadline_agent.extraction.fallback.AnthropicExtractor",
            mock_anthropic_cls,
            create=True,
        ):
            result = await extractor.extract(_sample_item())

    assert result is not None
    assert result.title == "Submit HW3"


@pytest.mark.asyncio
async def test_both_fail_returns_none() -> None:
    with patch("deadline_agent.extraction.fallback.settings") as mock_settings:
        mock_settings.use_anthropic_fallback = True
        mock_settings.anthropic_api_key = "test-key"

        from deadline_agent.extraction.fallback import FallbackExtractor

        extractor = FallbackExtractor()

    with (
        patch.object(extractor, "_ollama") as mock_ollama,
        patch(
            "deadline_agent.extraction.anthropic_extractor.AnthropicExtractor",
            create=True,
        ) as mock_anthropic_cls,
    ):
        mock_ollama.extract = AsyncMock(side_effect=ExtractionError("Ollama down"))
        mock_anthropic = AsyncMock()
        mock_anthropic.extract = AsyncMock(side_effect=ExtractionError("API error"))
        mock_anthropic_cls.return_value = mock_anthropic

        with patch(
            "deadline_agent.extraction.fallback.AnthropicExtractor",
            mock_anthropic_cls,
            create=True,
        ):
            result = await extractor.extract(_sample_item())

    assert result is None


@pytest.mark.asyncio
async def test_no_api_key_no_fallback() -> None:
    with patch("deadline_agent.extraction.fallback.settings") as mock_settings:
        mock_settings.use_anthropic_fallback = True
        mock_settings.anthropic_api_key = None

        from deadline_agent.extraction.fallback import FallbackExtractor

        extractor = FallbackExtractor()

    with patch.object(extractor, "_ollama") as mock_ollama:
        mock_ollama.extract = AsyncMock(side_effect=ExtractionError("Ollama down"))
        result = await extractor.extract(_sample_item())

    assert result is None


@pytest.mark.asyncio
async def test_ollama_low_confidence_no_fallback() -> None:
    """Low confidence returns None but doesn't trigger fallback (privacy per ADR-001)."""
    with patch("deadline_agent.extraction.fallback.settings") as mock_settings:
        mock_settings.use_anthropic_fallback = True
        mock_settings.anthropic_api_key = "test-key"

        from deadline_agent.extraction.fallback import FallbackExtractor

        extractor = FallbackExtractor()

    with patch.object(extractor, "_ollama") as mock_ollama:
        # Returns None (low confidence), doesn't raise ExtractionError
        mock_ollama.extract = AsyncMock(return_value=None)
        result = await extractor.extract(_sample_item())

    assert result is None
