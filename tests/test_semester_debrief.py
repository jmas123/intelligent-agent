"""Tests for semester debrief analytics."""

from datetime import UTC, datetime, timedelta

from deadline_agent.models import Task, WorkSession
from deadline_agent.reasoning.semester_debrief import (
    compute_semester_stats,
    stats_to_prompt,
)


def _make_task(session, title, course, status="done", task_type="assignment"):
    """Helper to create a task within the test semester."""
    task = Task(
        title=title,
        source="gmail",
        type=task_type,
        course=course,
        urgency_score=3,
        confidence=0.9,
        raw_hash=f"debrief_{title}_{course}",
        status=status,
    )
    session.add(task)
    session.flush()
    return task


def test_compute_semester_stats_basic(session):
    """Basic stats with completed and slipped tasks."""
    now = datetime.now(UTC)
    start = (now - timedelta(days=90)).isoformat()
    end = now.isoformat()

    _make_task(session, "HW1", "CS101", "done")
    _make_task(session, "HW2", "CS101", "done")
    t3 = _make_task(session, "Exam", "CS201", "pending", "exam")
    # Make t3 overdue
    t3.due_date_iso = (now - timedelta(days=1)).isoformat()
    session.commit()

    stats = compute_semester_stats(session, start, end)
    assert stats["tasks_completed"] == 2
    assert stats["tasks_slipped"] == 1
    assert stats["tasks_total"] == 3
    assert "CS101" in stats["by_course"]
    assert "CS201" in stats["by_course"]


def test_compute_semester_stats_with_work_sessions(session):
    """Stats include total work hours from work sessions."""
    now = datetime.now(UTC)
    start = (now - timedelta(days=90)).isoformat()
    end = now.isoformat()

    task = _make_task(session, "HW1", "CS101")
    ws = WorkSession(
        task_id=task.id,
        started_at=now - timedelta(hours=3),
        ended_at=now - timedelta(hours=1),
        duration_minutes=120,
    )
    session.add(ws)
    session.commit()

    stats = compute_semester_stats(session, start, end)
    assert stats["total_work_minutes"] == 120
    assert stats["total_work_hours"] == 2.0


def test_compute_semester_stats_empty(session):
    """Empty semester returns zero counts."""
    stats = compute_semester_stats(
        session, "2020-01-01", "2020-06-01"
    )
    assert stats["tasks_completed"] == 0
    assert stats["tasks_slipped"] == 0
    assert stats["total_work_minutes"] == 0


def test_workload_distribution(session):
    """Workload distribution groups tasks by week."""
    now = datetime.now(UTC)
    start = (now - timedelta(days=28)).isoformat()
    end = now.isoformat()

    # Create tasks spread across weeks
    for i in range(4):
        t = _make_task(session, f"Task{i}", "CS101")
        t.updated_at = now - timedelta(days=i * 7 + 1)
    session.commit()

    stats = compute_semester_stats(session, start, end)
    dist = stats["workload_distribution"]
    assert len(dist) >= 4  # At least 4 weeks
    total = sum(w["tasks_completed"] for w in dist)
    assert total == 4


def test_stats_to_prompt_formatting(session):
    """stats_to_prompt produces readable text."""
    stats = {
        "tasks_completed": 10,
        "tasks_slipped": 2,
        "tasks_total": 15,
        "total_work_hours": 50.5,
        "by_course": {
            "CS101": {"total": 8, "done": 6, "slipped": 1},
            "CS201": {"total": 7, "done": 4, "slipped": 1},
        },
        "lead_time_by_course": {"CS101": 3.5},
        "effort_accuracy_by_type": {
            "assignment": {
                "estimated_hours": 2.0,
                "actual_hours": 3.0,
                "ratio": 1.5,
                "sample_count": 5,
            }
        },
        "crunch_by_course": {
            "CS101": {
                "avg_days_before_deadline": 2.1,
                "last_minute_count": 1,
                "total_tasks": 6,
                "crunch_ratio": 0.17,
            }
        },
        "workload_distribution": [
            {"week": 1, "start": "Jan 10", "tasks_completed": 3},
        ],
        "procrastination_trend": {
            "first_half_avg_days": 3.0,
            "second_half_avg_days": 4.5,
            "trend": "improving",
        },
    }

    text = stats_to_prompt(stats)
    assert "Tasks completed: 10" in text
    assert "CS101" in text
    assert "EFFORT ACCURACY" in text
    assert "CRUNCH ANALYSIS" in text
    assert "PROCRASTINATION TREND" in text
    assert "improving" in text
