"""Tests for the state snapshot builder."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from deadline_agent.models import FileActivity, FileTaskLink, Task
from deadline_agent.reasoning.state import build_state_snapshot


def _add_task(session: Session, hours_until_due: int = 4, **overrides: object) -> Task:
    due = datetime.now(UTC) + timedelta(hours=hours_until_due)
    defaults: dict[str, object] = {
        "title": "Submit HW3",
        "due_date_iso": due.isoformat(),
        "source": "gmail",
        "type": "assignment",
        "course": "CS 101",
        "urgency_score": 4,
        "confidence": 0.92,
        "raw_hash": f"hash_{hours_until_due}_{id(overrides)}",
        "status": "pending",
    }
    defaults.update(overrides)
    task = Task(**defaults)
    session.add(task)
    session.flush()
    return task


def test_build_snapshot_empty(session: Session) -> None:
    snapshot = build_state_snapshot(session)
    assert snapshot.total_pending == 0
    assert snapshot.total_done == 0
    assert len(snapshot.due_today) == 0
    assert len(snapshot.overdue) == 0


def test_build_snapshot_with_tasks(session: Session) -> None:
    from zoneinfo import ZoneInfo
    # Use explicit local-day times to avoid day-boundary issues at late hours
    local_now = datetime.now(UTC).astimezone(ZoneInfo("America/New_York"))
    later_today = local_now.replace(hour=23, minute=30, second=0, microsecond=0)
    hours_to_later = (later_today - local_now).total_seconds() / 3600
    _add_task(session, hours_until_due=int(hours_to_later) or 1, raw_hash="today1")  # due today
    _add_task(session, hours_until_due=72, raw_hash="week1")  # due this week
    _add_task(session, hours_until_due=-36, raw_hash="overdue1")  # overdue (yesterday)

    snapshot = build_state_snapshot(session)
    assert snapshot.total_pending == 3
    assert len(snapshot.due_today) == 1
    assert len(snapshot.due_this_week) == 1
    assert len(snapshot.overdue) == 1


def test_build_snapshot_unworked_deadlines(session: Session) -> None:
    task = _add_task(session, hours_until_due=4, raw_hash="unworked1")
    # No file activity linked

    snapshot = build_state_snapshot(session)
    assert len(snapshot.unworked_deadlines) == 1
    assert snapshot.unworked_deadlines[0].id == task.id


def test_build_snapshot_worked_deadline_excluded(session: Session) -> None:
    task = _add_task(session, hours_until_due=4, raw_hash="worked1")

    # Add linked file activity
    activity = FileActivity(
        path="/Users/test/hw3.pdf",
        filename="hw3.pdf",
        directory="/Users/test",
        size_bytes=1024,
        modified_at=datetime.now(),
        event_type="modified",
    )
    session.add(activity)
    session.flush()
    link = FileTaskLink(
        file_activity_id=activity.id,
        task_id=task.id,
        confidence=0.8,
        method="string",
    )
    session.add(link)
    session.flush()

    snapshot = build_state_snapshot(session)
    assert len(snapshot.unworked_deadlines) == 0


def test_to_prompt_format(session: Session) -> None:
    from zoneinfo import ZoneInfo
    # Create a task due later today (in local TZ) to ensure DUE TODAY appears
    local_now = datetime.now(UTC).astimezone(ZoneInfo("America/New_York"))
    later_today = local_now.replace(hour=23, minute=30, second=0, microsecond=0)
    hours_to_later = max(1, int((later_today - local_now).total_seconds() / 3600))
    _add_task(session, hours_until_due=hours_to_later, raw_hash="prompt1")
    _add_task(session, hours_until_due=-36, raw_hash="prompt2")  # yesterday = overdue

    snapshot = build_state_snapshot(session)
    prompt = snapshot.to_prompt()

    assert "Current time:" in prompt
    assert "DUE TODAY" in prompt
    assert "OVERDUE" in prompt
    assert "Submit HW3" in prompt


def test_to_prompt_empty(session: Session) -> None:
    snapshot = build_state_snapshot(session)
    prompt = snapshot.to_prompt()
    assert "Current time:" in prompt
    assert "0 pending" in prompt
