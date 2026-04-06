"""Tests for behavioral pattern analyzer."""

import json
from datetime import datetime, timedelta

from deadline_agent.behavioral.analyzer import (
    analyze_effort_accuracy,
    analyze_lead_time,
    analyze_peak_hours,
    analyze_session_duration,
    analyze_work_by_time,
    run_all_analyses,
)
from deadline_agent.models import FileActivity, FileTaskLink, Task, WorkSession


def _make_done_task(session, title, raw_hash, task_type="assignment"):
    task = Task(
        title=title,
        source="gmail",
        type=task_type,
        urgency_score=3,
        confidence=0.9,
        raw_hash=raw_hash,
        status="done",
        created_at=datetime(2026, 3, 1, 10, 0, 0),
    )
    session.add(task)
    session.flush()
    return task


def _add_work_session(session, task_id, started_at, duration_minutes):
    ws = WorkSession(
        task_id=task_id,
        started_at=started_at,
        ended_at=started_at + timedelta(minutes=duration_minutes),
        duration_minutes=duration_minutes,
        file_activity_count=3,
    )
    session.add(ws)
    session.flush()
    return ws


def _add_file_activity(session, task, modified_at):
    fa = FileActivity(
        path=f"/tmp/{task.title}.pdf",
        filename=f"{task.title}.pdf",
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


def test_effort_accuracy(session):
    """Ratio of actual vs estimated effort for assignments."""
    task = _make_done_task(session, "HW10", "analyzer_1")
    # assignment base=120, urgency=3, multiplier=1.0 → estimated=120
    # actual: 200 minutes → ratio = 200/120 ≈ 1.67
    _add_work_session(session, task.id, datetime(2026, 3, 10, 10, 0), 120)
    _add_work_session(session, task.id, datetime(2026, 3, 10, 14, 0), 80)

    patterns = analyze_effort_accuracy(session)
    assert len(patterns) == 1
    data = json.loads(patterns[0].value)
    assert abs(data["ratio"] - 1.67) < 0.1
    assert patterns[0].pattern_key == "assignment"
    assert patterns[0].sample_count == 1


def test_peak_hours(session):
    """Detects hours with above-average activity."""
    # Create activities clustered at hours 10 and 14
    for i in range(20):
        fa = FileActivity(
            path=f"/tmp/file_{i}.pdf",
            filename=f"file_{i}.pdf",
            directory="/tmp",
            size_bytes=1024,
            modified_at=datetime(2026, 3, 10, 10, i, 0),
            event_type="modified",
        )
        session.add(fa)
    for i in range(15):
        fa = FileActivity(
            path=f"/tmp/file_b_{i}.pdf",
            filename=f"file_b_{i}.pdf",
            directory="/tmp",
            size_bytes=1024,
            modified_at=datetime(2026, 3, 11, 14, i, 0),
            event_type="modified",
        )
        session.add(fa)
    session.flush()

    patterns = analyze_peak_hours(session)
    assert len(patterns) >= 2
    # Keys are now window ranges like "10-11", "14-15"
    keys = {p.pattern_key for p in patterns}
    assert any("10" in k for k in keys)
    assert any("14" in k for k in keys)
    # Primary window should be the one with most activity (hour 10, 20 edits)
    import json
    primary = [p for p in patterns if json.loads(p.value).get("rank") == "primary"]
    assert len(primary) == 1


def test_lead_time(session):
    """Days between task creation and first file activity."""
    task = _make_done_task(session, "HW11", "analyzer_3")
    # task.created_at = March 1, first activity = March 3 → ~48 hours
    _add_file_activity(session, task, datetime(2026, 3, 3, 10, 0, 0))

    patterns = analyze_lead_time(session)
    assert len(patterns) == 1
    data = json.loads(patterns[0].value)
    assert abs(data["mean_hours"] - 48) < 1


def test_session_duration(session):
    """Average work session duration per task type."""
    task = _make_done_task(session, "HW12", "analyzer_4")
    _add_work_session(session, task.id, datetime(2026, 3, 10, 10, 0), 60)
    _add_work_session(session, task.id, datetime(2026, 3, 10, 14, 0), 90)

    patterns = analyze_session_duration(session)
    assert len(patterns) == 1
    data = json.loads(patterns[0].value)
    assert data["mean_minutes"] == 75.0
    assert data["total_sessions"] == 2


def test_work_by_time(session):
    """Cross-references task types with time-of-day."""
    # Assignment work in the evening (7-9 PM)
    hw = _make_done_task(session, "HW14", "analyzer_6", task_type="assignment")
    for i in range(5):
        _add_file_activity(session, hw, datetime(2026, 3, 10, 19, i * 10, 0))
    for i in range(3):
        _add_file_activity(session, hw, datetime(2026, 3, 11, 20, i * 10, 0))

    # Exam prep in the morning (9-11 AM)
    exam = _make_done_task(session, "Midterm", "analyzer_7", task_type="exam")
    for i in range(4):
        _add_file_activity(session, exam, datetime(2026, 3, 12, 9, i * 15, 0))
    for i in range(3):
        _add_file_activity(session, exam, datetime(2026, 3, 13, 10, i * 15, 0))

    patterns = analyze_work_by_time(session)
    assert len(patterns) == 2

    by_key = {p.pattern_key: p for p in patterns}
    assert "assignment" in by_key
    assert "exam" in by_key

    hw_data = json.loads(by_key["assignment"].value)
    assert hw_data["start_hour"] == 19  # 7 PM
    assert "7 PM" in hw_data["primary_window"]

    exam_data = json.loads(by_key["exam"].value)
    assert exam_data["start_hour"] == 9  # 9 AM


def test_work_by_time_with_life_tracks(session):
    """Life-track tagged activities (no task link) appear in work_by_time."""
    # Recruiting activity in the afternoon (2-4 PM), no task link
    for i in range(5):
        fa = FileActivity(
            path=f"/tmp/cover_letter_{i}.pdf",
            filename=f"cover_letter_{i}.pdf",
            directory="/tmp",
            size_bytes=1024,
            modified_at=datetime(2026, 3, 10, 14, i * 10, 0),
            event_type="modified",
            life_track="recruiting",
        )
        session.add(fa)
    session.flush()

    patterns = analyze_work_by_time(session)
    by_key = {p.pattern_key: p for p in patterns}
    assert "recruiting" in by_key
    data = json.loads(by_key["recruiting"].value)
    assert data["start_hour"] == 14


def test_run_all_analyses_smoke(session):
    """Smoke test: runs without error even with minimal data."""
    task = _make_done_task(session, "HW13", "analyzer_5")
    _add_work_session(session, task.id, datetime(2026, 3, 10, 10, 0), 60)
    _add_file_activity(session, task, datetime(2026, 3, 5, 10, 0, 0))

    patterns = run_all_analyses(session)
    assert len(patterns) >= 1
