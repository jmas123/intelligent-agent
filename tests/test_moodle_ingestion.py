"""Tests for the Moodle iCal ingester."""

from unittest.mock import AsyncMock, patch

import pytest

from deadline_agent.ingestion.moodle import MoodleIngester
from deadline_agent.pipeline import pipeline_queue

SAMPLE_ICAL = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Moodle//EN
BEGIN:VEVENT
SUMMARY:HW3 - Data Structures
DESCRIPTION:Submit via Moodle by end of day
DTSTART:20260325T235900Z
DTEND:20260325T235900Z
UID:event-101-hw3@moodle
END:VEVENT
BEGIN:VEVENT
SUMMARY:Midterm Exam
DESCRIPTION:Covers chapters 1-5
DTSTART:20260401T140000Z
DTEND:20260401T160000Z
UID:event-102-midterm@moodle
END:VEVENT
END:VCALENDAR
"""


class TestParseIcal:
    def test_parses_events(self) -> None:
        ingester = MoodleIngester(feed_urls=[])
        items = ingester._parse_ical(SAMPLE_ICAL)
        assert len(items) == 2

    def test_event_fields(self) -> None:
        ingester = MoodleIngester(feed_urls=[])
        items = ingester._parse_ical(SAMPLE_ICAL)
        hw = items[0]
        assert hw.source == "moodle"
        assert "HW3" in hw.raw_content
        assert hw.metadata["subject"] == "HW3 - Data Structures"
        assert hw.metadata["due_date_iso"] is not None
        assert hw.metadata["raw_hash"]  # non-empty hash

    def test_uid_produces_consistent_hash(self) -> None:
        ingester = MoodleIngester(feed_urls=[])
        items1 = ingester._parse_ical(SAMPLE_ICAL)
        items2 = ingester._parse_ical(SAMPLE_ICAL)
        assert items1[0].metadata["raw_hash"] == items2[0].metadata["raw_hash"]


class TestFetchAndEnqueue:
    @pytest.mark.asyncio
    async def test_fetches_and_enqueues(self) -> None:
        ingester = MoodleIngester(feed_urls=["https://moodle.example.com/calendar/export.ics"])

        mock_response = AsyncMock()
        mock_response.text = SAMPLE_ICAL
        mock_response.raise_for_status = lambda: None

        # Drain queue
        while not pipeline_queue.empty():
            pipeline_queue.get_nowait()

        with patch("deadline_agent.ingestion.moodle.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            count = await ingester.fetch_and_enqueue()

        assert count == 2
        assert not pipeline_queue.empty()
