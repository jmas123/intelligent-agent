"""Anthropic API-based structured extraction (fallback for Ollama)."""

import logging
from typing import Any

from anthropic import AsyncAnthropic

from deadline_agent.config import settings
from deadline_agent.extraction.extractor import (
    CONFIDENCE_DISCARD,
    CONFIDENCE_REVIEW,
    EXTRACTION_SYSTEM_PROMPT,
    ExtractionError,
)
from deadline_agent.extraction.schemas import ExtractedTask
from deadline_agent.pipeline import IngestItem

logger = logging.getLogger(__name__)

EXTRACT_TOOL: dict[str, Any] = {
    "name": "extract_task",
    "description": "Extract a structured task from the provided content.",
    "input_schema": ExtractedTask.model_json_schema(),
}


class AnthropicExtractor:
    """Extract tasks using Anthropic's API via tool calling."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._model = model or settings.anthropic_extraction_model
        key = api_key or settings.anthropic_api_key
        if not key:
            raise ValueError("Anthropic API key is required")
        self._client = AsyncAnthropic(api_key=key)

    async def extract(self, item: IngestItem) -> ExtractedTask | None:
        """Run structured extraction via Anthropic tool calling.

        Returns None if confidence < 0.6 or validation fails.
        Raises ExtractionError if the API is unreachable.
        """
        user_prompt = self._build_prompt(item)

        try:
            response = await self._client.messages.create(  # type: ignore[call-overload]
                model=self._model,
                max_tokens=1024,
                system=EXTRACTION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                tools=[EXTRACT_TOOL],
                tool_choice={"type": "tool", "name": "extract_task"},
            )
        except Exception as e:
            raise ExtractionError(f"Anthropic extraction failed: {e}") from e

        # Find the tool_use block in the response
        tool_input: dict[str, Any] | None = None
        for block in response.content:
            if hasattr(block, "type") and block.type == "tool_use":
                tool_input = block.input
                break

        if tool_input is None:
            logger.warning("Anthropic response contained no tool_use block")
            return None

        try:
            task = ExtractedTask.model_validate(tool_input)
        except Exception:
            logger.warning("Failed to validate Anthropic extraction: %s", tool_input)
            return None

        # Override source and raw_hash — don't trust the model
        task.source = item.source
        task.raw_hash = item.metadata.get("raw_hash", task.raw_hash)

        # Fall back to metadata due date if model missed it
        if task.due_date_iso is None and item.metadata.get("due_date_iso"):
            task.due_date_iso = item.metadata["due_date_iso"]

        # Fall back to metadata course if model missed it
        if not task.course and item.metadata.get("course"):
            task.course = item.metadata["course"]

        if task.confidence < CONFIDENCE_DISCARD:
            logger.info("Discarding low-confidence extraction: %.2f", task.confidence)
            return None

        if task.confidence < CONFIDENCE_REVIEW:
            logger.warning("Flagging for review (confidence %.2f): %s", task.confidence, task.title)

        return task

    def _build_prompt(self, item: IngestItem) -> str:
        """Build the user prompt from IngestItem metadata and content."""
        parts: list[str] = []
        if subject := item.metadata.get("subject"):
            parts.append(f"Subject: {subject}")
        if sender := item.metadata.get("sender"):
            parts.append(f"From: {sender}")
        if due := item.metadata.get("due_date_iso"):
            parts.append(f"Due date hint: {due}")
        if course := item.metadata.get("course"):
            parts.append(f"Course: {course}")
        parts.append(f"Content:\n{item.raw_content}")
        return "\n".join(parts)
