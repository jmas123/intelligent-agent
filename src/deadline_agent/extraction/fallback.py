"""Fallback extraction: Ollama first, then Anthropic if Ollama is unreachable."""

import logging

from deadline_agent.config import settings
from deadline_agent.extraction.extractor import ExtractionError, OllamaExtractor
from deadline_agent.extraction.schemas import ExtractedTask
from deadline_agent.pipeline import IngestItem

logger = logging.getLogger(__name__)


class FallbackExtractor:
    """Tries Ollama first, falls back to Anthropic on connection failure.

    Does NOT fall back on low confidence — that's intentional per ADR-001 (privacy).
    Only falls back when Ollama is unreachable (ExtractionError).
    """

    def __init__(self) -> None:
        self._ollama = OllamaExtractor()
        self._anthropic_available = (
            settings.use_anthropic_fallback and settings.anthropic_api_key is not None
        )
        if self._anthropic_available:
            logger.info("Anthropic fallback enabled")

    async def extract(self, item: IngestItem) -> ExtractedTask | None:
        """Extract a task, falling back to Anthropic if Ollama fails."""
        try:
            result = await self._ollama.extract(item)
            return result
        except ExtractionError:
            logger.warning("Ollama unreachable, attempting Anthropic fallback")

        if not self._anthropic_available:
            logger.error("Ollama failed and no Anthropic fallback configured")
            return None

        # Lazy import to avoid loading Anthropic SDK when not needed
        from deadline_agent.extraction.anthropic_extractor import AnthropicExtractor

        try:
            anthropic = AnthropicExtractor()
            result = await anthropic.extract(item)
            if result is not None:
                logger.info("Anthropic fallback succeeded: %s", result.title)
            return result
        except ExtractionError:
            logger.error("Both Ollama and Anthropic extraction failed")
            return None
