"""Base interface for all ingestion sources."""

from abc import ABC, abstractmethod
from typing import Any


class BaseIngester(ABC):
    """Abstract base class that all ingestion sources must implement."""

    @abstractmethod
    async def handle_webhook(self, payload: dict[str, Any]) -> None:
        """Process incoming webhook payload.

        Must not store raw content — pass to filter layer immediately.
        """
        ...

    @abstractmethod
    async def validate_payload(self, payload: dict[str, Any]) -> bool:
        """Validate that the payload is well-formed before processing."""
        ...
