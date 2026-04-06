"""Moodle iCal feed reader."""

import hashlib
import logging

import httpx
from icalendar import Calendar

from deadline_agent.pipeline import IngestItem, pipeline_queue

logger = logging.getLogger(__name__)


class MoodleIngester:
    """Fetches and parses Moodle iCal feeds.

    Pull-based on schedule — the exception to push-only (per ADR-003).
    """

    def __init__(self, feed_urls: list[str]) -> None:
        self._feed_urls = feed_urls

    async def fetch_and_enqueue(self) -> int:
        """Fetch all configured iCal feeds, parse events, enqueue items.

        Returns the number of items enqueued.
        """
        count = 0
        async with httpx.AsyncClient() as client:
            for url in self._feed_urls:
                try:
                    response = await client.get(url, timeout=30.0)
                    response.raise_for_status()
                except httpx.HTTPError:
                    logger.exception("Failed to fetch iCal feed: %s", url)
                    continue

                items = self._parse_ical(response.text)
                for item in items:
                    await pipeline_queue.put(item)
                    count += 1

        logger.info("Enqueued %d items from %d Moodle feeds", count, len(self._feed_urls))
        return count

    def _parse_ical(self, ical_text: str) -> list[IngestItem]:
        """Parse iCal text into IngestItem objects."""
        cal = Calendar.from_ical(ical_text)
        items: list[IngestItem] = []

        for component in cal.walk("VEVENT"):
            summary = str(component.get("SUMMARY", ""))
            description = str(component.get("DESCRIPTION", ""))

            # Skip "opens" events — only "closes" (due dates) matter
            summary_lower = summary.lower()
            if "opens" in summary_lower or "open" in summary_lower and "close" not in summary_lower:
                continue

            # Clean up suffix so titles read better
            for suffix in [" closes", " is due"]:
                if summary.lower().endswith(suffix):
                    summary = summary[: -len(suffix)]
                    break

            # Extract course name from CATEGORIES (Moodle puts it there)
            categories = component.get("CATEGORIES")
            course = None
            if categories:
                cat_values = (
                    categories.to_ical().decode()
                    if hasattr(categories, "to_ical")
                    else str(categories)
                )
                course = cat_values.split(",")[0].strip() if cat_values else None

            dtend = component.get("DTEND")
            dtstart = component.get("DTSTART")
            uid = str(component.get("UID", ""))

            due_dt = dtend.dt if dtend else (dtstart.dt if dtstart else None)
            due_iso = due_dt.isoformat() if due_dt else None

            raw_hash = hashlib.sha256(uid.encode()).hexdigest()

            items.append(
                IngestItem(
                    source="moodle",
                    raw_content=f"{summary}\n{description}",
                    metadata={
                        "subject": summary,
                        "sender": "moodle",
                        "due_date_iso": due_iso,
                        "course": course,
                        "uid": uid,
                        "raw_hash": raw_hash,
                    },
                )
            )

        return items
