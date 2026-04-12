"""Google Calendar FreeBusy API integration for finding free time windows."""

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

USER_TZ = ZoneInfo("America/New_York")

from deadline_agent.auth import TokenManager

logger = logging.getLogger(__name__)

CALENDAR_API = "https://www.googleapis.com/calendar/v3"


async def fetch_free_busy(
    start_iso: str,
    end_iso: str,
    token_manager: TokenManager | None = None,
) -> list[dict[str, str]]:
    """Fetch busy blocks from Google Calendar FreeBusy API.

    Returns list of {start, end} ISO strings for busy periods.
    Returns empty list if API is unavailable.
    """
    tm = token_manager or TokenManager()
    token = await tm.get_valid_token()
    if not token:
        logger.warning("No Google token — cannot fetch calendar gaps")
        return []

    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "timeMin": start_iso,
        "timeMax": end_iso,
        "items": [{"id": "primary"}],
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{CALENDAR_API}/freeBusy",
                json=body,
                headers=headers,
                timeout=10.0,
            )
            if resp.status_code != 200:
                logger.error("FreeBusy API error %d: %s", resp.status_code, resp.text)
                return []
            data = resp.json()
            calendars = data.get("calendars", {})
            primary = calendars.get("primary", {})
            busy: list[dict[str, str]] = primary.get("busy", [])
            return busy
    except httpx.HTTPError:
        logger.exception("Failed to fetch FreeBusy data")
        return []


async def fetch_events(
    start_iso: str,
    end_iso: str,
    token_manager: TokenManager | None = None,
) -> list[dict[str, str]]:
    """Fetch calendar events from Google Calendar Events API.

    Returns list of {summary, start, end, location} dicts for the given range.
    Returns empty list if API is unavailable.
    """
    tm = token_manager or TokenManager()
    token = await tm.get_valid_token()
    if not token:
        logger.warning("No Google token — cannot fetch calendar events")
        return []

    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "timeMin": start_iso,
        "timeMax": end_iso,
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": "25",
    }

    try:
        async with httpx.AsyncClient() as client:
            # Fetch calendar list to query all calendars
            cal_resp = await client.get(
                f"{CALENDAR_API}/users/me/calendarList",
                headers=headers,
                timeout=10.0,
            )
            if cal_resp.status_code != 200:
                calendar_ids = ["primary"]
            else:
                calendar_ids = [
                    c["id"] for c in cal_resp.json().get("items", [])
                    if c.get("selected", True)  # only calendars the user has enabled
                ]
                if not calendar_ids:
                    calendar_ids = ["primary"]

            events: list[dict[str, str]] = []
            for cal_id in calendar_ids:
                resp = await client.get(
                    f"{CALENDAR_API}/calendars/{cal_id}/events",
                    params=params,
                    headers=headers,
                    timeout=10.0,
                )
                if resp.status_code != 200:
                    logger.debug("Calendar Events API error for %s: %d", cal_id, resp.status_code)
                    continue
                for item in resp.json().get("items", []):
                    start = item.get("start", {})
                    end = item.get("end", {})
                    events.append({
                        "summary": item.get("summary", "(No title)"),
                        "start": start.get("dateTime") or start.get("date", ""),
                        "end": end.get("dateTime") or end.get("date", ""),
                        "location": item.get("location", ""),
                        "description": item.get("description", ""),
                        "attendees": [
                            {
                                "email": a.get("email", ""),
                                "displayName": a.get("displayName", ""),
                                "responseStatus": a.get("responseStatus", ""),
                                "self": a.get("self", False),
                            }
                            for a in item.get("attendees", [])
                        ],
                    })

            # Sort by start time
            events.sort(key=lambda e: e.get("start", ""))
            return events
    except httpx.HTTPError:
        logger.exception("Failed to fetch calendar events")
        return []


def find_gaps(
    busy_blocks: list[dict[str, str]],
    start_iso: str,
    end_iso: str,
    min_minutes: int = 60,
) -> list[dict[str, Any]]:
    """Compute free windows between busy blocks.

    Returns list of {start, end, duration_minutes} for gaps >= min_minutes.
    Only returns gaps during reasonable hours (8am-10pm).
    """
    start = datetime.fromisoformat(start_iso).astimezone(USER_TZ)
    end = datetime.fromisoformat(end_iso).astimezone(USER_TZ)

    # Sort busy blocks by start time
    sorted_busy = sorted(busy_blocks, key=lambda b: b["start"])

    gaps: list[dict[str, Any]] = []
    cursor = start

    for block in sorted_busy:
        block_start = datetime.fromisoformat(block["start"]).astimezone(USER_TZ)
        block_end = datetime.fromisoformat(block["end"]).astimezone(USER_TZ)

        if block_start > cursor:
            # There's a gap between cursor and block_start
            _add_reasonable_gaps(gaps, cursor, block_start, min_minutes)

        if block_end > cursor:
            cursor = block_end

    # Gap after last busy block
    if cursor < end:
        _add_reasonable_gaps(gaps, cursor, end, min_minutes)

    return gaps


def _add_reasonable_gaps(
    gaps: list[dict[str, Any]],
    gap_start: datetime,
    gap_end: datetime,
    min_minutes: int,
) -> None:
    """Add gaps that fall within reasonable hours (8am-10pm)."""
    # Clamp to reasonable hours
    day = gap_start.date()
    while day <= gap_end.date():
        day_start = datetime(day.year, day.month, day.day, 8, 0, tzinfo=gap_start.tzinfo)
        day_end = datetime(day.year, day.month, day.day, 22, 0, tzinfo=gap_start.tzinfo)

        window_start = max(gap_start, day_start)
        window_end = min(gap_end, day_end)

        if window_start < window_end:
            duration = (window_end - window_start).total_seconds() / 60
            if duration >= min_minutes:
                gaps.append(
                    {
                        "start": window_start.isoformat(),
                        "end": window_end.isoformat(),
                        "duration_minutes": int(duration),
                    }
                )

        day += timedelta(days=1)
