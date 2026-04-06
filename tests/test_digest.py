"""Tests for morning digest generation."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy.orm import Session

from deadline_agent.models import Task
from deadline_agent.notifications.digest import generate_digest, send_digest


def _add_task(
    session: Session,
    title: str,
    hours_until_due: float,
    course: str | None = None,
    urgency: int = 3,
) -> Task:
    due = datetime.now(UTC) + timedelta(hours=hours_until_due)
    task = Task(
        title=title,
        due_date_iso=due.isoformat(),
        source="gmail",
        type="assignment",
        course=course,
        urgency_score=urgency,
        confidence=0.9,
        raw_hash=f"hash-{title}",
    )
    session.add(task)
    session.commit()
    return task


class TestGenerateDigest:
    def test_generates_digest(self, session: Session) -> None:
        _add_task(session, "HW3", 24, course="CS 101", urgency=4)
        _add_task(session, "Essay", 48, urgency=2)
        text = generate_digest(session)
        assert text is not None
        assert "2 upcoming deadline(s)" in text
        assert "HW3" in text
        assert "CS 101" in text

    def test_no_tasks_returns_none(self, session: Session) -> None:
        text = generate_digest(session)
        assert text is None

    def test_excludes_past_due(self, session: Session) -> None:
        _add_task(session, "Past Task", -5)
        _add_task(session, "Future Task", 24)
        text = generate_digest(session)
        assert text is not None
        assert "Past Task" not in text
        assert "Future Task" in text

    def test_excludes_done_tasks(self, session: Session) -> None:
        task = _add_task(session, "Done Task", 24)
        task.status = "done"
        session.commit()
        text = generate_digest(session)
        assert text is None

    def test_urgency_indicators(self, session: Session) -> None:
        _add_task(session, "Urgent", 24, urgency=5)
        text = generate_digest(session)
        assert text is not None
        assert "[!!!!!]" in text


class TestSendDigest:
    @patch("deadline_agent.notifications.digest.send_notification", return_value=True)
    def test_sends_digest(self, mock_notify: object, session: Session) -> None:
        _add_task(session, "HW3", 24)
        result = send_digest(session)
        assert result is True

    @patch("deadline_agent.notifications.digest.send_notification", return_value=True)
    def test_no_tasks_returns_false(self, mock_notify: object, session: Session) -> None:
        result = send_digest(session)
        assert result is False
