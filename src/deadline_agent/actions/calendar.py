"""Google Calendar event creation."""

import logging
from typing import Any

import httpx

from deadline_agent.auth import TokenManager

logger = logging.getLogger(__name__)

CALENDAR_API = "https://www.googleapis.com/calendar/v3"


async def create_calendar_event(
    payload: dict[str, Any],
    token_manager: TokenManager | None = None,
) -> dict[str, Any] | None:
    """Create a Google Calendar event.

    Payload: {summary, start_iso, end_iso, description?}
    Returns created event data or None on failure.
    """
    tm = token_manager or TokenManager()
    token = await tm.get_valid_token()
    if not token:
        logger.error("No valid Google token for calendar write")
        return None

    event_body = {
        "summary": payload["summary"],
        "start": {"dateTime": payload["start_iso"], "timeZone": "America/New_York"},
        "end": {"dateTime": payload["end_iso"], "timeZone": "America/New_York"},
    }
    if desc := payload.get("description"):
        event_body["description"] = desc

    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{CALENDAR_API}/calendars/primary/events",
                json=event_body,
                headers=headers,
                timeout=10.0,
            )
            if resp.status_code not in (200, 201):
                logger.error("Calendar API error %d: %s", resp.status_code, resp.text)
                return None
            result: dict[str, Any] = resp.json()
            logger.info("Created calendar event: %s", result.get("id"))
            return result
    except httpx.HTTPError:
        logger.exception("Failed to create calendar event")
        return None
