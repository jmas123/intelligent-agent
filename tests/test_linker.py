"""Tests for the heuristic file-to-task linker."""

from datetime import datetime

from sqlalchemy.orm import Session

from deadline_agent.awareness.linker import TaskLinker, _course_in_path, _title_keywords_in_filename
from deadline_agent.models import FileActivity, Task


def _add_task(session: Session, **overrides: object) -> Task:
    defaults = {
        "title": "Submit HW3",
        "due_date_iso": "2026-03-25T23:59:00",
        "source": "gmail",
        "type": "assignment",
        "course": "CS 101",
        "urgency_score": 4,
        "confidence": 0.92,
        "raw_hash": "abc123",
        "status": "pending",
    }
    defaults.update(overrides)
    task = Task(**defaults)
    session.add(task)
    session.flush()
    return task


def _add_activity(session: Session, **overrides: object) -> FileActivity:
    defaults = {
        "path": "/Users/test/Documents/CS101/hw3.pdf",
        "filename": "hw3.pdf",
        "directory": "/Users/test/Documents/CS101",
        "size_bytes": 1024,
        "modified_at": datetime(2026, 3, 24, 10, 0, 0),
        "event_type": "modified",
    }
    defaults.update(overrides)
    activity = FileActivity(**defaults)
    session.add(activity)
    session.flush()
    return activity


# --- Unit tests for helper functions ---


def test_course_in_path_compact() -> None:
    assert _course_in_path("CS 101", "/Users/test/Documents/CS101") is True


def test_course_in_path_spaced() -> None:
    assert _course_in_path("CS 101", "/Users/test/Documents/cs/101") is True


def test_course_in_path_no_match() -> None:
    assert _course_in_path("CS 101", "/Users/test/Documents/Math200") is False


def test_title_keywords_in_filename_full_match() -> None:
    score = _title_keywords_in_filename("HW3 Report", "hw3-report.pdf")
    assert score > 0.5


def test_title_keywords_in_filename_partial() -> None:
    score = _title_keywords_in_filename("Final Project Report", "report.docx")
    assert 0 < score < 1.0


def test_title_keywords_in_filename_no_match() -> None:
    score = _title_keywords_in_filename("Submit HW3", "vacation_photos.jpg")
    assert score == 0.0


# --- Integration tests with TaskLinker ---


def test_linker_matches_course_in_path(session: Session) -> None:
    task = _add_task(session, course="CS 101", raw_hash="t1")
    activity = _add_activity(
        session,
        path="/Users/test/Documents/CS101/draft.pdf",
        filename="draft.pdf",
        directory="/Users/test/Documents/CS101",
    )

    linker = TaskLinker(session)
    matches = linker.find_matching_tasks(activity)

    assert len(matches) == 1
    assert matches[0][0].id == task.id
    assert matches[0][1] >= 0.7  # course match gives 0.7
    assert matches[0][2] == "string"


def test_linker_matches_title_and_course(session: Session) -> None:
    _add_task(session, title="HW3 Report", course="CS 101", raw_hash="t1")
    activity = _add_activity(
        session,
        path="/Users/test/Documents/CS101/hw3-report.pdf",
        filename="hw3-report.pdf",
        directory="/Users/test/Documents/CS101",
    )

    linker = TaskLinker(session)
    matches = linker.find_matching_tasks(activity)

    assert len(matches) == 1
    # Course (0.7) + title overlap bonus
    assert matches[0][1] > 0.7


def test_linker_no_match_for_unrelated_file(session: Session) -> None:
    _add_task(session, course="CS 101", raw_hash="t1")
    activity = _add_activity(
        session,
        path="/Users/test/Downloads/movie.mp4",
        filename="movie.mp4",
        directory="/Users/test/Downloads",
    )

    linker = TaskLinker(session)
    matches = linker.find_matching_tasks(activity)

    assert len(matches) == 0


def test_linker_skips_done_tasks(session: Session) -> None:
    _add_task(session, course="CS 101", status="done", raw_hash="t1")
    activity = _add_activity(
        session,
        directory="/Users/test/Documents/CS101",
    )

    linker = TaskLinker(session)
    matches = linker.find_matching_tasks(activity)

    assert len(matches) == 0


def test_linker_skips_tasks_without_due_date(session: Session) -> None:
    _add_task(session, course="CS 101", due_date_iso=None, raw_hash="t1")
    activity = _add_activity(
        session,
        directory="/Users/test/Documents/CS101",
    )

    linker = TaskLinker(session)
    matches = linker.find_matching_tasks(activity)

    assert len(matches) == 0


def test_linker_multiple_tasks_ranked(session: Session) -> None:
    _add_task(session, title="HW3 Report", course="CS 101", raw_hash="t1")
    _add_task(session, title="Lab 5", course="Math 200", raw_hash="t2")

    activity = _add_activity(
        session,
        path="/Users/test/Documents/CS101/hw3-report.pdf",
        filename="hw3-report.pdf",
        directory="/Users/test/Documents/CS101",
    )

    linker = TaskLinker(session)
    matches = linker.find_matching_tasks(activity)

    # Only CS 101 task should match
    assert len(matches) == 1
    assert matches[0][0].title == "HW3 Report"
