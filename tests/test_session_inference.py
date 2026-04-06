"""Tests for session inference from file activity."""

from datetime import datetime, timedelta

from deadline_agent.behavioral.session_inference import infer_sessions
from deadline_agent.models import FileActivity, FileTaskLink, Task


def _make_task(session, title, raw_hash):
    task = Task(
        title=title,
        source="gmail",
        type="assignment",
        urgency_score=3,
        confidence=0.9,
        raw_hash=raw_hash,
        status="pending",
    )
    session.add(task)
    session.flush()
    return task


def _make_activity(session, task, modified_at):
    fa = FileActivity(
        path=f"/tmp/{task.title.lower().replace(' ', '_')}.pdf",
        filename=f"{task.title.lower().replace(' ', '_')}.pdf",
        directory="/tmp",
        size_bytes=1024,
        modified_at=modified_at,
        event_type="modified",
    )
    session.add(fa)
    session.flush()
    link = FileTaskLink(
        file_activity_id=fa.id,
        task_id=task.id,
        confidence=0.8,
        method="string",
    )
    session.add(link)
    session.flush()
    return fa


def test_clusters_by_gap(session):
    """Activities within 30 min form one session; 45-min gap splits into two."""
    task = _make_task(session, "HW5", "inf_test_1")
    base = datetime(2026, 3, 15, 10, 0, 0)

    # Cluster 1: 10:00, 10:10, 10:20
    _make_activity(session, task, base)
    _make_activity(session, task, base + timedelta(minutes=10))
    _make_activity(session, task, base + timedelta(minutes=20))

    # Gap of 45 minutes

    # Cluster 2: 11:05, 11:15
    _make_activity(session, task, base + timedelta(minutes=65))
    _make_activity(session, task, base + timedelta(minutes=75))

    work_sessions = infer_sessions(session)
    assert len(work_sessions) == 2

    assert work_sessions[0].duration_minutes == 20
    assert work_sessions[0].file_activity_count == 3
    assert work_sessions[1].duration_minutes == 10
    assert work_sessions[1].file_activity_count == 2


def test_idempotent(session):
    """Running inference twice produces no duplicates."""
    task = _make_task(session, "HW6", "inf_test_2")
    base = datetime(2026, 3, 15, 14, 0, 0)
    _make_activity(session, task, base)
    _make_activity(session, task, base + timedelta(minutes=20))

    first_run = infer_sessions(session)
    assert len(first_run) == 1

    second_run = infer_sessions(session)
    assert len(second_run) == 0


def test_different_tasks_separated(session):
    """Activities for different tasks produce separate sessions."""
    task_a = _make_task(session, "HW7a", "inf_test_3a")
    task_b = _make_task(session, "HW7b", "inf_test_3b")
    base = datetime(2026, 3, 15, 10, 0, 0)

    # Interleaved activities
    _make_activity(session, task_a, base)
    _make_activity(session, task_b, base + timedelta(minutes=5))
    _make_activity(session, task_a, base + timedelta(minutes=10))
    _make_activity(session, task_b, base + timedelta(minutes=15))

    work_sessions = infer_sessions(session)
    assert len(work_sessions) == 2

    task_ids = {ws.task_id for ws in work_sessions}
    assert task_ids == {task_a.id, task_b.id}


def test_single_touch_skipped(session):
    """A single file activity (0 duration) is below MIN_SESSION_MINUTES."""
    task = _make_task(session, "HW8", "inf_test_4")
    _make_activity(session, task, datetime(2026, 3, 15, 10, 0, 0))

    work_sessions = infer_sessions(session)
    assert len(work_sessions) == 0
