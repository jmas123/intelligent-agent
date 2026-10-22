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


def _mock_engine():
    engine = AsyncMock()
    engine.evaluate = AsyncMock()
    return engine


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
async def test_briefing_with_overdue_and_due_today(mock_get_engine):
    mock_engine = _mock_engine()
    mock_get_engine.return_value = mock_engine

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

    event = Event(
        type=SESSION_STARTED,
        payload={"life_track": "school", "timestamp": now.isoformat()},
    )
    await gen._on_session_started(event)

    mock_engine.evaluate.assert_called_once()
    call_kwargs = mock_engine.evaluate.call_args.kwargs
    assert call_kwargs["category"] == "briefing"
    assert call_kwargs["title"] == "Session briefing"


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
async def test_briefing_with_life_context(mock_get_engine):
    mock_engine = _mock_engine()
    mock_get_engine.return_value = mock_engine

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

    event = Event(
        type=SESSION_STARTED,
        payload={"life_track": "school", "timestamp": now.isoformat()},
    )
    await gen._on_session_started(event)

    mock_engine.evaluate.assert_called_once()


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
async def test_briefing_dedup(mock_get_engine):
    mock_engine = _mock_engine()
    mock_get_engine.return_value = mock_engine

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

    ts = now.isoformat()
    event = Event(
        type=SESSION_STARTED,
        payload={"life_track": "school", "timestamp": ts},
    )
    # First call generates briefing
    await gen._on_session_started(event)
    first_count = mock_engine.evaluate.call_count

    # Second call with same timestamp is deduped
    await gen._on_session_started(event)
    assert mock_engine.evaluate.call_count == first_count


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
async def test_briefing_empty_state(mock_get_engine):
    mock_engine = _mock_engine()
    mock_get_engine.return_value = mock_engine

    factory, session = _make_session_factory()

    gen = SessionBriefingGenerator(factory)

    now = datetime.now(UTC)
    event = Event(
        type=SESSION_STARTED,
        payload={"life_track": "school", "timestamp": now.isoformat()},
    )
    await gen._on_session_started(event)

    # No tasks → no briefing
    mock_engine.evaluate.assert_not_called()
