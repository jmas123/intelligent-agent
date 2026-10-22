"""Tests for no-work-detected alerts."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from deadline_agent.models import FileActivity, FileTaskLink, Task
from deadline_agent.notifications.no_work_alerts import check_no_work_alerts


def _add_task(session: Session, hours_until_due: int = 48, **overrides: object) -> Task:
    due = datetime.now(UTC) + timedelta(hours=hours_until_due)
    defaults: dict[str, object] = {
        "title": "Submit HW3",
        "due_date_iso": due.isoformat(),
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


def _add_linked_activity(session: Session, task: Task) -> None:
    activity = FileActivity(
        path="/Users/test/Documents/CS101/hw3.pdf",
        filename="hw3.pdf",
        directory="/Users/test/Documents/CS101",
        size_bytes=1024,
        modified_at=datetime.now(),
        event_type="modified",
    )
    session.add(activity)
    session.flush()
    link = FileTaskLink(
        file_activity_id=activity.id,
        task_id=task.id,
        confidence=0.8,
        method="string",
    )
    session.add(link)
    session.flush()


def _mock_engine():
    engine = AsyncMock()
    engine.evaluate = AsyncMock()
    return engine


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
async def test_alerts_for_task_with_no_work(mock_get_engine: object, session: Session) -> None:
    mock_get_engine.return_value = _mock_engine()
    _add_task(session, hours_until_due=48)

    count = await check_no_work_alerts(session)
    assert count == 1


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
async def test_no_alert_when_work_exists(mock_get_engine: object, session: Session) -> None:
    mock_get_engine.return_value = _mock_engine()
    task = _add_task(session, hours_until_due=48)
    _add_linked_activity(session, task)

    count = await check_no_work_alerts(session)
    assert count == 0


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
async def test_no_alert_when_already_sent(mock_get_engine: object, session: Session) -> None:
    mock_get_engine.return_value = _mock_engine()
    _add_task(session, hours_until_due=48, no_work_alert_sent=True, raw_hash="sent1")

    count = await check_no_work_alerts(session)
    assert count == 0


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
async def test_no_alert_for_done_tasks(mock_get_engine: object, session: Session) -> None:
    mock_get_engine.return_value = _mock_engine()
    _add_task(session, hours_until_due=48, status="done", raw_hash="done1")

    count = await check_no_work_alerts(session)
    assert count == 0


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
async def test_no_alert_for_far_future_tasks(mock_get_engine: object, session: Session) -> None:
    mock_get_engine.return_value = _mock_engine()
    _add_task(session, hours_until_due=200, raw_hash="far1")

    count = await check_no_work_alerts(session)
    assert count == 0


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
async def test_no_alert_for_past_due_tasks(mock_get_engine: object, session: Session) -> None:
    mock_get_engine.return_value = _mock_engine()
    _add_task(session, hours_until_due=-5, raw_hash="past1")

    count = await check_no_work_alerts(session)
    assert count == 0


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
async def test_sets_no_work_alert_sent_flag(mock_get_engine: object, session: Session) -> None:
    mock_get_engine.return_value = _mock_engine()
    task = _add_task(session, hours_until_due=48)

    await check_no_work_alerts(session)
    session.refresh(task)
    assert task.no_work_alert_sent is True


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
async def test_multiple_tasks(mock_get_engine: object, session: Session) -> None:
    mock_get_engine.return_value = _mock_engine()
    _add_task(session, hours_until_due=24, raw_hash="t1")
    _add_task(session, hours_until_due=48, raw_hash="t2", title="Lab 5", course="Math 200")
    task_with_work = _add_task(session, hours_until_due=48, raw_hash="t3", title="Quiz Prep")
    _add_linked_activity(session, task_with_work)

    count = await check_no_work_alerts(session)
    assert count == 2  # Two tasks without work
