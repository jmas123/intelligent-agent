"""Tests for causal reasoning computations."""

from datetime import UTC, datetime, timedelta

from deadline_agent.models import Base, BehavioralPattern, Task, WorkSession
from deadline_agent.reasoning.causal import (
    compute_causal_context,
    compute_effort_context,
)

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def _make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _make_task(session, title="HW4", task_type="assignment", course="CS 101", due_days=2, urgency=3):
    due = (datetime.now(UTC) + timedelta(days=due_days)).isoformat()
    t = Task(
        title=title,
        due_date_iso=due,
        source="test",
        type=task_type,
        course=course,
        urgency_score=urgency,
        confidence=0.9,
        raw_hash=f"hash_{title}_{due}",
    )
    session.add(t)
    session.flush()
    return t


def _make_pattern(session, pattern_type, pattern_key, value, confidence=0.8):
    import json
    p = BehavioralPattern(
        pattern_type=pattern_type,
        pattern_key=pattern_key,
        value=json.dumps(value),
        sample_count=10,
        confidence=confidence,
    )
    session.add(p)
    session.flush()
    return p


def test_causal_context_with_lead_time():
    session = _make_session()
    task = _make_task(session, due_days=1)  # Due tomorrow
    pattern = _make_pattern(session, "lead_time", "assignment", {"mean_hours": 72})  # 3 days lead
    session.commit()

    now = datetime.now(UTC)
    stmts = compute_causal_context([task], [pattern], now, session)
    assert len(stmts) == 1
    assert "lead time" in stmts[0].lower()
    assert "HW4" in stmts[0]


def test_causal_context_with_work_started():
    session = _make_session()
    task = _make_task(session, due_days=1)
    pattern = _make_pattern(session, "lead_time", "assignment", {"mean_hours": 72})

    # Add a work session
    ws = WorkSession(
        task_id=task.id,
        started_at=datetime.now(UTC) - timedelta(hours=2),
        ended_at=datetime.now(UTC) - timedelta(hours=1),
        duration_minutes=60,
    )
    session.add(ws)
    session.commit()

    now = datetime.now(UTC)
    stmts = compute_causal_context([task], [pattern], now, session)
    assert len(stmts) == 1
    assert "underway" in stmts[0].lower()


def test_causal_context_no_patterns():
    session = _make_session()
    task = _make_task(session, due_days=1)
    session.commit()

    now = datetime.now(UTC)
    stmts = compute_causal_context([task], [], now, session)
    assert stmts == []


def test_causal_context_with_procrastination_fallback():
    session = _make_session()
    task = _make_task(session, due_days=0.5)  # Due in 12 hours
    pattern = _make_pattern(session, "procrastination", "assignment", {"mean_days_before_deadline": 2.0})
    session.commit()

    now = datetime.now(UTC)
    stmts = compute_causal_context([task], [pattern], now, session)
    assert len(stmts) == 1
    assert "typically start" in stmts[0].lower()


def test_effort_context_with_accuracy_pattern():
    session = _make_session()
    task = _make_task(session, due_days=2, urgency=3)
    pattern = _make_pattern(session, "effort_accuracy", "assignment", {"ratio": 1.4})
    session.commit()

    stmts = compute_effort_context([task], [pattern], session)
    assert len(stmts) == 1
    assert "1.4x" in stmts[0]
    assert "HW4" in stmts[0]


def test_effort_context_accurate_estimate():
    session = _make_session()
    task = _make_task(session, due_days=2)
    # Ratio close to 1.0 — should not generate a statement
    pattern = _make_pattern(session, "effort_accuracy", "assignment", {"ratio": 1.05})
    session.commit()

    stmts = compute_effort_context([task], [pattern], session)
    assert stmts == []
