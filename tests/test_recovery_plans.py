"""Tests for recovery plan generation."""

from datetime import UTC, datetime, timedelta

from deadline_agent.models import Base, BehavioralPattern, Task
from deadline_agent.reasoning.recovery import (
    RecoveryPlan,
    RecoveryPlanResponse,
    _build_recovery_prompt,
    _get_peak_window,
)

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def _make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _make_task(session, title="HW4", due_days=-1, urgency=4):
    """Create a task. Negative due_days = overdue."""
    t = Task(
        title=title,
        due_date_iso=(datetime.now(UTC) + timedelta(days=due_days)).isoformat(),
        source="test",
        type="assignment",
        course="CS 101",
        urgency_score=urgency,
        confidence=0.9,
        raw_hash=f"recovery_hash_{title}_{due_days}",
    )
    session.add(t)
    session.flush()
    return t


def test_get_peak_window():
    patterns = [
        BehavioralPattern(
            pattern_type="peak_hours",
            pattern_key="19-21",
            value=json.dumps({"rank": "primary", "label": "7 PM–9 PM"}),
            sample_count=50,
            confidence=0.9,
        ),
    ]
    assert _get_peak_window(patterns) == "7 PM–9 PM"


def test_get_peak_window_none():
    assert _get_peak_window([]) is None


def test_build_recovery_prompt():
    session = _make_session()
    task = _make_task(session, due_days=-2)
    session.commit()

    patterns = [
        BehavioralPattern(
            pattern_type="peak_hours",
            pattern_key="19-21",
            value=json.dumps({"rank": "primary", "label": "7 PM–9 PM"}),
            sample_count=50,
            confidence=0.9,
        ),
    ]

    prompt = _build_recovery_prompt([task], [], patterns, session)
    assert "HW4" in prompt
    assert "7 PM–9 PM" in prompt
    assert "OVERDUE" in prompt


def test_build_recovery_prompt_with_gaps():
    session = _make_session()
    task = _make_task(session, due_days=1)
    session.commit()

    gaps = [
        {"start": "2026-04-07T14:00:00", "duration_minutes": 120},
        {"start": "2026-04-07T19:00:00", "duration_minutes": 90},
    ]

    prompt = _build_recovery_prompt([task], gaps, [], session)
    assert "AVAILABLE CALENDAR GAPS" in prompt
    assert "120min" in prompt


def test_recovery_plan_model():
    """Verify the RecoveryPlan pydantic model validates correctly."""
    from deadline_agent.reasoning.recovery import DailyBlock

    plan = RecoveryPlan(
        task_title="HW4",
        status="overdue",
        days_behind=2.0,
        estimated_hours_remaining=3.5,
        daily_blocks=[
            DailyBlock(day="Wednesday", time_window="7-9 PM", action="Draft outline"),
            DailyBlock(day="Thursday", time_window="7-9 PM", action="Complete and submit"),
        ],
        tradeoff_note="Deprioritize Discussion Post 5 — lower grade weight.",
    )
    assert plan.task_title == "HW4"
    assert len(plan.daily_blocks) == 2


def test_recovery_plan_response_schema():
    """Verify the schema can be generated for LLM structured output."""
    schema = RecoveryPlanResponse.model_json_schema()
    assert "plans" in schema["properties"]
    assert schema["required"] == ["plans"]
