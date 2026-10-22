"""Tests for pre-deadline alert engine."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
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


def _mock_engine():
    """Create a mock intervention engine."""
    engine = AsyncMock()
    engine.evaluate = AsyncMock()
    return engine


class TestCheckAndSendAlerts:
    @pytest.mark.asyncio
    @patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
    async def test_sends_24h_alert(self, mock_get_engine: object, session: Session) -> None:
        mock_get_engine.return_value = _mock_engine()
        _task_due_in(session, 20)  # 20 hours from now
        count = await check_and_send_alerts(session)
        assert count == 1

    @pytest.mark.asyncio
    @patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
    async def test_sends_2h_alert(self, mock_get_engine: object, session: Session) -> None:
        mock_get_engine.return_value = _mock_engine()
        _task_due_in(session, 1.5)  # 1.5 hours from now
        count = await check_and_send_alerts(session)
        assert count == 1

    @pytest.mark.asyncio
    @patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
    async def test_2h_alert_also_marks_24h(self, mock_get_engine: object, session: Session) -> None:
        mock_get_engine.return_value = _mock_engine()
        task = _task_due_in(session, 1)
        await check_and_send_alerts(session)
        session.refresh(task)
        assert task.alert_2h_sent is True
        assert task.alert_24h_sent is True

    @pytest.mark.asyncio
    @patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
    async def test_no_duplicate_alerts(self, mock_get_engine: object, session: Session) -> None:
        mock_get_engine.return_value = _mock_engine()
        _task_due_in(session, 20)
        await check_and_send_alerts(session)
        # Second run should send 0
        count = await check_and_send_alerts(session)
        assert count == 0

    @pytest.mark.asyncio
    @patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
    async def test_skips_past_due(self, mock_get_engine: object, session: Session) -> None:
        mock_get_engine.return_value = _mock_engine()
        _task_due_in(session, -5)  # 5 hours ago
        count = await check_and_send_alerts(session)
        assert count == 0

    @pytest.mark.asyncio
    @patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
    async def test_skips_far_future(self, mock_get_engine: object, session: Session) -> None:
        mock_get_engine.return_value = _mock_engine()
        _task_due_in(session, 48)  # 2 days from now
        count = await check_and_send_alerts(session)
        assert count == 0

    @pytest.mark.asyncio
    @patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
    async def test_skips_done_tasks(self, mock_get_engine: object, session: Session) -> None:
        mock_get_engine.return_value = _mock_engine()
        task = _task_due_in(session, 10)
        task.status = "done"
        session.commit()
        count = await check_and_send_alerts(session)
        assert count == 0

    @pytest.mark.asyncio
    @patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
    async def test_multiple_tasks(self, mock_get_engine: object, session: Session) -> None:
        mock_get_engine.return_value = _mock_engine()
        _task_due_in(session, 20, "Task A")
        _task_due_in(session, 1, "Task B")
        _task_due_in(session, 48, "Task C")  # too far
        count = await check_and_send_alerts(session)
        assert count == 2  # A gets 24h, B gets 2h
