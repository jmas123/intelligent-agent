"""Tests for proactive interrupt handler."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from deadline_agent.awareness.proactive_interrupts import ProactiveInterruptHandler
from deadline_agent.events import SESSION_STARTED, Event
from deadline_agent.models import Base, Task

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def _make_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)

    class Factory:
        def __call__(self):
            return session

        def __enter__(self):
            return session

        def __exit__(self, *args):
            pass

    factory = Factory()
    return factory, session


@pytest.mark.asyncio
async def test_interrupt_with_overdue_tasks():
    factory, session = _make_session_factory()

    now = datetime.now(UTC)
    task = Task(
        title="Overdue HW",
        due_date_iso=(now - timedelta(days=2)).isoformat(),
        source="test",
        type="assignment",
        urgency_score=4,
        confidence=0.9,
        raw_hash="interrupt_test_1",
    )
    session.add(task)
    session.commit()

    handler = ProactiveInterruptHandler(factory)

    with patch("deadline_agent.awareness.proactive_interrupts.notification_router") as mock_router:
        mock_router.send = AsyncMock(return_value=True)

        event = Event(
            type=SESSION_STARTED,
            payload={"life_track": "school", "project_dir": "/tmp", "timestamp": now.isoformat()},
        )
        await handler._on_session_started(event)

        mock_router.send.assert_called_once()
        notification = mock_router.send.call_args[0][0]
        assert "overdue" in notification.body.lower()
        assert notification.priority.value == "low"


@pytest.mark.asyncio
async def test_interrupt_no_pending_items():
    factory, session = _make_session_factory()

    handler = ProactiveInterruptHandler(factory)

    with patch("deadline_agent.awareness.proactive_interrupts.notification_router") as mock_router:
        mock_router.send = AsyncMock(return_value=True)

        event = Event(
            type=SESSION_STARTED,
            payload={"life_track": "school"},
        )
        await handler._on_session_started(event)

        # No pending items → no notification
        mock_router.send.assert_not_called()


@pytest.mark.asyncio
async def test_interrupt_includes_due_today():
    factory, session = _make_session_factory()

    now = datetime.now(UTC)
    task = Task(
        title="Due Today HW",
        due_date_iso=(now + timedelta(hours=6)).isoformat(),
        source="test",
        type="assignment",
        urgency_score=3,
        confidence=0.9,
        raw_hash="interrupt_test_2",
    )
    session.add(task)
    session.commit()

    handler = ProactiveInterruptHandler(factory)

    with patch("deadline_agent.awareness.proactive_interrupts.notification_router") as mock_router:
        mock_router.send = AsyncMock(return_value=True)

        event = Event(
            type=SESSION_STARTED,
            payload={"life_track": "school"},
        )
        await handler._on_session_started(event)

        mock_router.send.assert_called_once()
        assert "due today" in mock_router.send.call_args[0][0].body.lower()
