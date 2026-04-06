"""Tests for the pre-filter layer."""

from deadline_agent.filter.prefilter import (
    has_date_reference,
    has_deadline_keywords,
    matches_academic_sender,
    should_process,
)
from deadline_agent.pipeline import IngestItem


def _item(
    content: str = "",
    subject: str = "",
    sender: str = "",
    source: str = "gmail",
) -> IngestItem:
    return IngestItem(
        source=source,
        raw_content=content,
        metadata={"subject": subject, "sender": sender},
    )


class TestKeywords:
    def test_finds_deadline(self) -> None:
        assert has_deadline_keywords("Please submit your assignment by Friday")

    def test_finds_exam(self) -> None:
        assert has_deadline_keywords("Midterm exam next week")

    def test_rejects_marketing(self) -> None:
        assert not has_deadline_keywords("50% off all shoes this weekend!")

    def test_case_insensitive(self) -> None:
        assert has_deadline_keywords("DEADLINE approaching")


class TestSenderHeuristics:
    def test_edu_domain(self) -> None:
        assert matches_academic_sender("prof@university.edu")

    def test_moodle(self) -> None:
        assert matches_academic_sender("noreply@moodle.university.edu")

    def test_generic_sender(self) -> None:
        assert not matches_academic_sender("deals@amazon.com")

    def test_noreply_university(self) -> None:
        assert matches_academic_sender("noreply@university-system.org")


class TestDateReference:
    def test_iso_date(self) -> None:
        assert has_date_reference("Due by 2026-03-25")

    def test_us_date(self) -> None:
        assert has_date_reference("Submit before 3/25/2026")

    def test_month_day(self) -> None:
        assert has_date_reference("Due March 25, 2026")

    def test_tomorrow(self) -> None:
        assert has_date_reference("This is due tomorrow")

    def test_today_is_a_date(self) -> None:
        assert has_date_reference("This is due today")

    def test_no_date(self) -> None:
        assert not has_date_reference("Hello, how are you doing")


class TestShouldProcess:
    def test_passes_with_keywords(self) -> None:
        item = _item(content="Please submit your homework", sender="random@gmail.com")
        assert should_process(item)

    def test_passes_academic_sender_with_date(self) -> None:
        item = _item(content="Class moved to March 25", sender="prof@mit.edu")
        assert should_process(item)

    def test_rejects_academic_sender_without_date(self) -> None:
        item = _item(content="Welcome to the class", sender="prof@mit.edu")
        assert not should_process(item)

    def test_rejects_random_email(self) -> None:
        item = _item(content="Check out our new product", sender="sales@shop.com")
        assert not should_process(item)

    def test_keyword_in_subject(self) -> None:
        item = _item(subject="Assignment 3 posted", sender="random@gmail.com")
        assert should_process(item)
