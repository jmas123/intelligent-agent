"""Tests for context switch detection and handling."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from deadline_agent.awareness.context_switch_handler import ContextSwitchHandler
from deadline_agent.events import CONTEXT_SWITCH_DETECTED, Event, EventBus
from deadline_agent.models import Base, RecruitingApplication, Task

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
async def test_context_switch_to_recruiting(mock_get_engine):
    mock_engine = _mock_engine()
    mock_get_engine.return_value = mock_engine

    factory, session = _make_session_factory()

    # Add some active recruiting applications
    now = datetime.now(UTC)
    app = RecruitingApplication(
        company_name="Google",
        company_normalized="google",
        status="interview",
        last_signal_at=now - timedelta(days=3),
    )
    session.add(app)
    session.commit()

    handler = ContextSwitchHandler(factory)

    event = Event(
        type=CONTEXT_SWITCH_DETECTED,
        payload={"from_mode": "school", "to_mode": "recruiting"},
    )
    await handler._on_context_switch(event)

    mock_engine.evaluate.assert_called_once()
    call_kwargs = mock_engine.evaluate.call_args.kwargs
    assert "recruiting" in call_kwargs["title"].lower()
    assert call_kwargs["risk_level"] == "low"


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
async def test_context_switch_to_school(mock_get_engine):
    mock_engine = _mock_engine()
    mock_get_engine.return_value = mock_engine

    factory, session = _make_session_factory()

    # Add overdue tasks
    now = datetime.now(UTC)
    task = Task(
        title="HW5",
        due_date_iso=(now - timedelta(days=1)).isoformat(),
        source="test",
        type="assignment",
        urgency_score=4,
        confidence=0.9,
        raw_hash="ctx_switch_test_1",
    )
    session.add(task)
    session.commit()

    handler = ContextSwitchHandler(factory)

    event = Event(
        type=CONTEXT_SWITCH_DETECTED,
        payload={"from_mode": "recruiting", "to_mode": "school"},
    )
    await handler._on_context_switch(event)

    mock_engine.evaluate.assert_called_once()
    call_kwargs = mock_engine.evaluate.call_args.kwargs
    assert "overdue" in call_kwargs["body"].lower()


@pytest.mark.asyncio
@patch("deadline_agent.reasoning.intervention_engine.get_intervention_engine")
async def test_context_switch_no_notification_when_empty(mock_get_engine):
    mock_engine = _mock_engine()
    mock_get_engine.return_value = mock_engine

    factory, session = _make_session_factory()

    handler = ContextSwitchHandler(factory)

    event = Event(
        type=CONTEXT_SWITCH_DETECTED,
        payload={"from_mode": "school", "to_mode": "project"},
    )
    await handler._on_context_switch(event)

    # No pending tasks → no notification
    mock_engine.evaluate.assert_not_called()
