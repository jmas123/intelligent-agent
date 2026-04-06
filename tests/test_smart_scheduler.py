"""Tests for the smart scheduler."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from deadline_agent.models import Task
from deadline_agent.reasoning.scheduler import estimate_effort, propose_smart_schedule
from deadline_agent.reasoning.state import UnifiedContext


def _add_task(session: Session, **overrides: object) -> Task:
    due = datetime.now(UTC) + timedelta(hours=48)
    defaults: dict[str, object] = {
        "title": "Submit HW3",
        "due_date_iso": due.isoformat(),
        "source": "gmail",
        "type": "assignment",
        "course": "CS 101",
        "urgency_score": 4,
        "confidence": 0.92,
        "raw_hash": f"hash_{id(overrides)}",
        "status": "pending",
    }
    defaults.update(overrides)
    task = Task(**defaults)
    session.add(task)
    session.flush()
    return task


def test_estimate_effort_assignment() -> None:
    task = Task(
        title="HW3",
        type="assignment",
        urgency_score=3,
        source="gmail",
        confidence=0.9,
        raw_hash="x",
    )
    assert estimate_effort(task) == 120


def test_estimate_effort_exam() -> None:
    task = Task(
        title="Final",
        type="exam",
        urgency_score=5,
        source="gmail",
        confidence=0.9,
        raw_hash="x",
    )
    # 180 * 1.2 = 216
    assert estimate_effort(task) == 216


def test_estimate_effort_meeting() -> None:
    task = Task(
        title="Office Hours",
        type="meeting",
        urgency_score=2,
        source="gmail",
        confidence=0.9,
        raw_hash="x",
    )
    # 60 * 0.9 = 54
    assert estimate_effort(task) == 54


def test_propose_smart_schedule_basic(session: Session) -> None:
    task = _add_task(session, raw_hash="sched1", urgency_score=4)
    now = datetime.now(UTC)

    context = UnifiedContext(
        now=now,
        unworked_deadlines=[task],
        calendar_gaps=[
            {
                "start": (now + timedelta(hours=1)).isoformat(),
                "end": (now + timedelta(hours=4)).isoformat(),
                "duration_minutes": 180,
            }
        ],
    )

    proposals = propose_smart_schedule(context, session)
    assert len(proposals) == 1
    assert proposals[0].type == "calendar_block"
    assert proposals[0].task_id == task.id


def test_propose_smart_schedule_no_gaps(session: Session) -> None:
    task = _add_task(session, raw_hash="sched2")

    context = UnifiedContext(
        now=datetime.now(UTC),
        unworked_deadlines=[task],
        calendar_gaps=[],
    )

    proposals = propose_smart_schedule(context, session)
    assert len(proposals) == 0


def test_propose_smart_schedule_gap_too_small(session: Session) -> None:
    task = _add_task(session, raw_hash="sched3", type="exam", urgency_score=5)
    now = datetime.now(UTC)

    context = UnifiedContext(
        now=now,
        unworked_deadlines=[task],
        calendar_gaps=[
            {
                "start": (now + timedelta(hours=1)).isoformat(),
                "end": (now + timedelta(hours=2)).isoformat(),
                "duration_minutes": 60,
            }
        ],
    )

    # Exam needs ~216min, gap is only 60min
    proposals = propose_smart_schedule(context, session)
    assert len(proposals) == 0


def test_propose_smart_schedule_prioritizes_urgency(session: Session) -> None:
    low = _add_task(session, raw_hash="low", urgency_score=1, title="Low Priority")
    high = _add_task(session, raw_hash="high", urgency_score=5, title="Urgent")
    now = datetime.now(UTC)

    context = UnifiedContext(
        now=now,
        unworked_deadlines=[low, high],
        calendar_gaps=[
            {
                "start": (now + timedelta(hours=1)).isoformat(),
                "end": (now + timedelta(hours=5)).isoformat(),
                "duration_minutes": 240,
            }
        ],
    )

    proposals = propose_smart_schedule(context, session)
    # High urgency should be scheduled first
    assert len(proposals) >= 1
    assert proposals[0].task_id == high.id
