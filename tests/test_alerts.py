"""Tests for pre-deadline alert engine."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy.orm import Session

from deadline_agent.models import Task
from deadline_agent.notifications.alerts import check_and_send_alerts


def _task_due_in(session: Session, hours: float, title: str = "Test Task") -> Task:
    """Create a task due in N hours from now."""
    due = datetime.now(UTC) + timedelta(hours=hours)
    task = Task(
        title=title,
        due_date_iso=due.isoformat(),
        source="gmail",
        type="assignment",
        urgency_score=3,
        confidence=0.9,
        raw_hash=f"hash-{title}-{hours}",
    )
    session.add(task)
    session.commit()
    return task


class TestCheckAndSendAlerts:
    @patch("deadline_agent.notifications.alerts.send_notification", return_value=True)
    def test_sends_24h_alert(self, mock_notify: object, session: Session) -> None:
        _task_due_in(session, 20)  # 20 hours from now
        count = check_and_send_alerts(session)
        assert count == 1

    @patch("deadline_agent.notifications.alerts.send_notification", return_value=True)
    def test_sends_2h_alert(self, mock_notify: object, session: Session) -> None:
        _task_due_in(session, 1.5)  # 1.5 hours from now
        count = check_and_send_alerts(session)
        assert count == 1

    @patch("deadline_agent.notifications.alerts.send_notification", return_value=True)
    def test_2h_alert_also_marks_24h(self, mock_notify: object, session: Session) -> None:
        task = _task_due_in(session, 1)
        check_and_send_alerts(session)
        session.refresh(task)
        assert task.alert_2h_sent is True
        assert task.alert_24h_sent is True

    @patch("deadline_agent.notifications.alerts.send_notification", return_value=True)
    def test_no_duplicate_alerts(self, mock_notify: object, session: Session) -> None:
        _task_due_in(session, 20)
        check_and_send_alerts(session)
        # Second run should send 0
        count = check_and_send_alerts(session)
        assert count == 0

    @patch("deadline_agent.notifications.alerts.send_notification", return_value=True)
    def test_skips_past_due(self, mock_notify: object, session: Session) -> None:
        _task_due_in(session, -5)  # 5 hours ago
        count = check_and_send_alerts(session)
        assert count == 0

    @patch("deadline_agent.notifications.alerts.send_notification", return_value=True)
    def test_skips_far_future(self, mock_notify: object, session: Session) -> None:
        _task_due_in(session, 48)  # 2 days from now
        count = check_and_send_alerts(session)
        assert count == 0

    @patch("deadline_agent.notifications.alerts.send_notification", return_value=True)
    def test_skips_done_tasks(self, mock_notify: object, session: Session) -> None:
        task = _task_due_in(session, 10)
        task.status = "done"
        session.commit()
        count = check_and_send_alerts(session)
        assert count == 0

    @patch("deadline_agent.notifications.alerts.send_notification", return_value=True)
    def test_multiple_tasks(self, mock_notify: object, session: Session) -> None:
        _task_due_in(session, 20, "Task A")
        _task_due_in(session, 1, "Task B")
        _task_due_in(session, 48, "Task C")  # too far
        count = check_and_send_alerts(session)
        assert count == 2  # A gets 24h, B gets 2h
