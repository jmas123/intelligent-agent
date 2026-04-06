"""Tests for WorkSessionRepository."""

from datetime import datetime, timedelta

from deadline_agent.models import Task
from deadline_agent.store.session_repository import WorkSessionRepository


def test_create_and_get_for_task(session):
    """Creating a session and retrieving it by task_id."""
    task = Task(
        title="HW1",
        source="gmail",
        type="assignment",
        urgency_score=3,
        confidence=0.9,
        raw_hash="ws_test_1",
        status="pending",
    )
    session.add(task)
    session.flush()

    repo = WorkSessionRepository(session)
    now = datetime.utcnow()
    ws = repo.create(
        {
            "task_id": task.id,
            "started_at": now - timedelta(hours=2),
            "ended_at": now - timedelta(hours=1),
            "duration_minutes": 60,
            "file_activity_count": 5,
        }
    )

    assert ws.id is not None
    assert ws.duration_minutes == 60
    assert ws.file_activity_count == 5

    sessions = repo.get_for_task(task.id)
    assert len(sessions) == 1
    assert sessions[0].id == ws.id


def test_get_total_duration(session):
    """Sum of duration_minutes for a task."""
    task = Task(
        title="HW2",
        source="gmail",
        type="assignment",
        urgency_score=3,
        confidence=0.9,
        raw_hash="ws_test_2",
        status="done",
    )
    session.add(task)
    session.flush()

    repo = WorkSessionRepository(session)
    now = datetime.utcnow()
    repo.create(
        {
            "task_id": task.id,
            "started_at": now - timedelta(hours=4),
            "ended_at": now - timedelta(hours=3),
            "duration_minutes": 60,
        }
    )
    repo.create(
        {
            "task_id": task.id,
            "started_at": now - timedelta(hours=2),
            "ended_at": now - timedelta(hours=1),
            "duration_minutes": 45,
        }
    )

    assert repo.get_total_duration(task.id) == 105


def test_get_total_duration_no_sessions(session):
    """Returns 0 when no sessions exist."""
    repo = WorkSessionRepository(session)
    assert repo.get_total_duration(999) == 0


def test_already_inferred(session):
    """Dedup guard prevents duplicate sessions."""
    task = Task(
        title="HW3",
        source="gmail",
        type="assignment",
        urgency_score=3,
        confidence=0.9,
        raw_hash="ws_test_3",
        status="pending",
    )
    session.add(task)
    session.flush()

    repo = WorkSessionRepository(session)
    start = datetime(2026, 3, 15, 10, 0, 0)
    repo.create(
        {
            "task_id": task.id,
            "started_at": start,
            "ended_at": start + timedelta(hours=1),
            "duration_minutes": 60,
        }
    )

    assert repo.already_inferred(task.id, start) is True
    assert repo.already_inferred(task.id, start + timedelta(minutes=1)) is False


def test_get_recent(session):
    """Get sessions from the last N days."""
    task = Task(
        title="HW4",
        source="gmail",
        type="assignment",
        urgency_score=3,
        confidence=0.9,
        raw_hash="ws_test_4",
        status="done",
    )
    session.add(task)
    session.flush()

    repo = WorkSessionRepository(session)
    now = datetime.utcnow()
    # Recent session
    repo.create(
        {
            "task_id": task.id,
            "started_at": now - timedelta(days=1),
            "ended_at": now - timedelta(days=1, hours=-1),
            "duration_minutes": 60,
        }
    )
    # Old session
    repo.create(
        {
            "task_id": task.id,
            "started_at": now - timedelta(days=60),
            "ended_at": now - timedelta(days=60, hours=-1),
            "duration_minutes": 60,
        }
    )

    recent = repo.get_recent(days=30)
    assert len(recent) == 1
