"""Ollama-based structured extraction."""

import logging

from ollama import AsyncClient

from deadline_agent.config import settings
from deadline_agent.extraction.schemas import ExtractedTask
from deadline_agent.pipeline import IngestItem

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = (
    "You are a deadline extraction assistant. Given an email or calendar event, "
    "extract structured task information. Only extract if there is a clear deadline, "
    "assignment, exam, or actionable item. Set confidence low if you are unsure."
)

CONFIDENCE_DISCARD = 0.6
CONFIDENCE_REVIEW = 0.75


class ExtractionError(Exception):
    """Raised when the extraction service is unreachable or fails at the API level."""


class OllamaExtractor:
    """Extract tasks using Ollama's structured output (JSON schema)."""

    def __init__(
        self,
        model: str = "",
        base_url: str | None = None,
    ) -> None:
        self._model = model or settings.lora_extraction_model or settings.extraction_model
        self._client = AsyncClient(host=base_url or settings.ollama_base_url)

    async def extract(self, item: IngestItem) -> ExtractedTask | None:
        """Run structured extraction on an IngestItem.

        Returns None if confidence < 0.6 or validation fails.
        Raises ExtractionError if Ollama is unreachable.
        """
        user_prompt = self._build_prompt(item)

        try:
            response = await self._client.chat(
                model=self._model,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                format=ExtractedTask.model_json_schema(),
            )
        except Exception as e:
            raise ExtractionError(f"Ollama extraction failed: {e}") from e

        try:
            content: str = response.message.content or ""
            task = ExtractedTask.model_validate_json(content)
        except Exception:
            logger.warning("Failed to validate Ollama response: %s", response.message.content)
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
