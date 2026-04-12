"""Tests for session briefing generation."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from deadline_agent.awareness.session_briefing import SessionBriefingGenerator
from deadline_agent.events import SESSION_STARTED, Event
from deadline_agent.models import Base, LifeContext, Task

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
async def test_briefing_with_overdue_and_due_today():
    factory, session = _make_session_factory()

    now = datetime.now(UTC)
    # Overdue task
    session.add(Task(
        title="Overdue HW",
        due_date_iso=(now - timedelta(days=1)).isoformat(),
        source="test", type="assignment", urgency_score=4,
        confidence=0.9, raw_hash="briefing_test_1",
    ))
    # Due today task
    session.add(Task(
        title="Due Today HW",
        due_date_iso=(now + timedelta(hours=5)).isoformat(),
        source="test", type="assignment", urgency_score=3,
        confidence=0.9, raw_hash="briefing_test_2",
    ))
    session.commit()

    gen = SessionBriefingGenerator(factory)

    with patch("deadline_agent.awareness.session_briefing.notification_router") as mock_router:
        mock_router.send = AsyncMock(return_value=True)

        event = Event(
            type=SESSION_STARTED,
            payload={"life_track": "school", "timestamp": now.isoformat()},
        )
        await gen._on_session_started(event)

        assert mock_router.send.call_count >= 1
        first_notification = mock_router.send.call_args_list[0][0][0]
        assert first_notification.priority.value == "low"
        assert first_notification.category == "briefing"


@pytest.mark.asyncio
async def test_briefing_with_life_context():
    factory, session = _make_session_factory()

    now = datetime.now(UTC)
    session.add(LifeContext(
        season="exams",
        label="Final Exams",
        start_date=(now - timedelta(days=3)).strftime("%Y-%m-%d"),
        end_date=(now + timedelta(days=10)).strftime("%Y-%m-%d"),
        source="auto",
        active=True,
    ))
    session.add(Task(
        title="Midterm",
        due_date_iso=(now + timedelta(hours=3)).isoformat(),
        source="test", type="exam", urgency_score=5,
        confidence=0.9, raw_hash="briefing_test_3",
    ))
    session.commit()

    gen = SessionBriefingGenerator(factory)

    with patch("deadline_agent.awareness.session_briefing.notification_router") as mock_router:
        mock_router.send = AsyncMock(return_value=True)

        event = Event(
            type=SESSION_STARTED,
            payload={"life_track": "school", "timestamp": now.isoformat()},
        )
        await gen._on_session_started(event)

        assert mock_router.send.call_count >= 1


@pytest.mark.asyncio
async def test_briefing_dedup():
    factory, session = _make_session_factory()

    now = datetime.now(UTC)
    session.add(Task(
        title="Task",
        due_date_iso=(now + timedelta(hours=3)).isoformat(),
        source="test", type="assignment", urgency_score=3,
        confidence=0.9, raw_hash="briefing_test_4",
    ))
    session.commit()

    gen = SessionBriefingGenerator(factory)

    with patch("deadline_agent.awareness.session_briefing.notification_router") as mock_router:
        mock_router.send = AsyncMock(return_value=True)

        ts = now.isoformat()
        event = Event(
            type=SESSION_STARTED,
            payload={"life_track": "school", "timestamp": ts},
        )
        # First call generates briefing
        await gen._on_session_started(event)
        first_count = mock_router.send.call_count

        # Second call with same timestamp is deduped
        await gen._on_session_started(event)
        assert mock_router.send.call_count == first_count


@pytest.mark.asyncio
async def test_briefing_empty_state():
    factory, session = _make_session_factory()

    gen = SessionBriefingGenerator(factory)

    with patch("deadline_agent.awareness.session_briefing.notification_router") as mock_router:
        mock_router.send = AsyncMock(return_value=True)

        now = datetime.now(UTC)
        event = Event(
            type=SESSION_STARTED,
            payload={"life_track": "school", "timestamp": now.isoformat()},
        )
        await gen._on_session_started(event)

        # No tasks → no briefing
        mock_router.send.assert_not_called()
