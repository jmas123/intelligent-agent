"""Google Calendar push webhook ingester."""

import base64
import hashlib
import json
import logging
from typing import Any

import httpx

from deadline_agent.auth import TokenManager
from deadline_agent.ingestion.base import BaseIngester
from deadline_agent.pipeline import IngestItem, pipeline_queue

logger = logging.getLogger(__name__)


class GCalIngester(BaseIngester):
    """Handles Google Calendar push notifications.

    Flow: receive Pub/Sub notification → fetch events via Calendar API → enqueue.
    """

    def __init__(self, token_manager: TokenManager | None = None) -> None:
        self._token_manager = token_manager or TokenManager()
        self._api_base = "https://www.googleapis.com/calendar/v3"

    async def validate_payload(self, payload: dict[str, Any]) -> bool:
        """Validate Pub/Sub envelope structure."""
        msg = payload.get("message")
        if not isinstance(msg, dict):
            return False
        return "data" in msg and "messageId" in msg

    async def handle_webhook(self, payload: dict[str, Any]) -> None:
        """Decode notification, fetch calendar events, enqueue."""
        data_b64 = payload["message"]["data"]
        data = json.loads(base64.b64decode(data_b64))
        calendar_id = data.get("calendarId", "primary")

        events = await self._fetch_recent_events(calendar_id)
        for event in events:
            event_id = event.get("id", "")
            raw_hash = hashlib.sha256(event_id.encode()).hexdigest()

            summary = event.get("summary", "")
            description = event.get("description", "")
            start = event.get("start", {})
            end = event.get("end", {})
            organizer = event.get("organizer", {})

            # Use dateTime if available, fall back to date (all-day events)
            start_iso = start.get("dateTime") or start.get("date")
            end_iso = end.get("dateTime") or end.get("date")

            item = IngestItem(
                source="gcal",
                raw_content=f"{summary}\n{description}",
                metadata={
                    "subject": summary,
                    "sender": organizer.get("email", ""),
                    "due_date_iso": end_iso or start_iso,
                    "start_iso": start_iso,
                    "event_id": event_id,
                    "raw_hash": raw_hash,
                },
            )
            await pipeline_queue.put(item)

    async def _fetch_recent_events(self, calendar_id: str) -> list[dict[str, Any]]:
        """Fetch recent events from Google Calendar API."""
        token = await self._token_manager.get_valid_token()
        if not token:
            logger.error("No valid Google token available")
            return []

        headers = {"Authorization": f"Bearer {token}"}

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._api_base}/calendars/{calendar_id}/events",
                    params={
                        "maxResults": 10,
                        "singleEvents": "true",
                        "orderBy": "updated",
                    },
                    headers=headers,
                    timeout=10.0,
                )
                if resp.status_code != 200:
                    logger.error("Calendar API error: %s", resp.text)
                    return []

                return resp.json().get("items", [])  # type: ignore[no-any-return]
        except httpx.HTTPError:
            logger.exception("Failed to fetch calendar events")
            return []
