"""Tests for Phase 24: Task affect inference and energy proxy."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from deadline_agent.awareness.task_affect import (
    TaskAffectResult,
    classify_task_affect,
    compute_energy_proxy,
    get_affect_for_task,
    get_intervention,
    get_task_affect_map,
)
from deadline_agent.models import Task, WorkSession


# ── Helpers ──────────────────────────────────────────────────


def _add_task(
    session: Session,
    *,
    task_type: str = "assignment",
    hours_until_due: int = 168,  # 1 week
    status: str = "done",
    created_hours_ago: int = 168,
    raw_hash: str | None = None,
) -> Task:
    now = datetime.now(UTC)
    created = now - timedelta(hours=created_hours_ago)
    due = now + timedelta(hours=hours_until_due)
    task = Task(
        title=f"Test {task_type}",
        due_date_iso=due.isoformat(),
        source="gmail",
        type=task_type,
        course="CS 101",
        urgency_score=3,
        confidence=0.9,
        raw_hash=raw_hash or f"hash_{id(session)}_{task_type}_{hours_until_due}_{created_hours_ago}",
        status=status,
        created_at=created,
    )
    session.add(task)
    session.flush()
    return task


def _add_session(
    session: Session,
    task: Task,
    *,
    hours_after_created: int = 24,
    duration_minutes: int = 60,
) -> WorkSession:
    started = task.created_at + timedelta(hours=hours_after_created)
    ended = started + timedelta(minutes=duration_minutes)
    ws = WorkSession(
        task_id=task.id,
        started_at=started,
        ended_at=ended,
        duration_minutes=duration_minutes,
        file_activity_count=5,
    )
    session.add(ws)
    session.flush()
    return ws


# ── Classification tests ─────────────────────────────────────


def test_classify_enjoyment(session: Session) -> None:
    """Tasks with early starts and high completion rate -> enjoyment."""
    for i in range(5):
        t = _add_task(
            session,
            task_type="project",
            hours_until_due=168,
            created_hours_ago=200,
            status="done",
            raw_hash=f"enjoy_{i}",
        )
        # Start early: 10h after created (out of ~200h available) = ~5% lag
        _add_session(session, t, hours_after_created=10)

    session.commit()
    patterns = classify_task_affect(session)

    affect_map = get_task_affect_map(session)
    assert "project" in affect_map
    assert affect_map["project"].affect_label == "enjoyment"


def test_classify_avoidance(session: Session) -> None:
    """Tasks started in the final 10% of available time -> avoidance."""
    for i in range(5):
        t = _add_task(
            session,
            task_type="assignment",
            hours_until_due=10,  # due in 10h
            created_hours_ago=168,  # created 168h ago -> 178h total available
            status="done",
            raw_hash=f"avoid_{i}",
        )
        # Start very late: 170h after created (out of ~178h) = ~95% lag
        _add_session(session, t, hours_after_created=170)

    session.commit()
    classify_task_affect(session)

    affect_map = get_task_affect_map(session)
    assert "assignment" in affect_map
    assert affect_map["assignment"].affect_label == "avoidance"


def test_classify_anxiety(session: Session) -> None:
    """Tasks with no work sessions (majority skipped) -> anxiety."""
    for i in range(5):
        _add_task(
            session,
            task_type="exam",
            status="pending",
            raw_hash=f"anxiety_{i}",
        )
        # No work sessions added

    session.commit()
    classify_task_affect(session)

    affect_map = get_task_affect_map(session)
    assert "exam" in affect_map
    assert affect_map["exam"].affect_label == "anxiety"


def test_classify_neutral(session: Session) -> None:
    """Tasks with moderate start timing -> neutral."""
    for i in range(5):
        t = _add_task(
            session,
            task_type="assignment",
            hours_until_due=100,
            created_hours_ago=168,
            status="done",
            raw_hash=f"neutral_{i}",
        )
        # Start at ~50% of available time
        _add_session(session, t, hours_after_created=130)

    session.commit()
    classify_task_affect(session)

    affect_map = get_task_affect_map(session)
    assert "assignment" in affect_map
    assert affect_map["assignment"].affect_label == "neutral"


def test_no_due_date_excluded(session: Session) -> None:
    """Tasks without due dates are excluded from classification."""
    for i in range(5):
        task = Task(
            title="No due date task",
            due_date_iso=None,
            source="gmail",
            type="reminder",
            course=None,
            urgency_score=1,
            confidence=0.5,
            raw_hash=f"nodue_{i}",
            status="done",
        )
        session.add(task)
    session.commit()

    classify_task_affect(session)
    affect_map = get_task_affect_map(session)
    assert "reminder" not in affect_map


def test_insufficient_samples_excluded(session: Session) -> None:
    """Types with fewer than 3 samples are excluded."""
    for i in range(2):
        t = _add_task(
            session,
            task_type="networking",
            raw_hash=f"few_{i}",
        )
        _add_session(session, t, hours_after_created=10)
    session.commit()

    classify_task_affect(session)
    affect_map = get_task_affect_map(session)
    assert "networking" not in affect_map


# ── Energy proxy tests ───────────────────────────────────────


def test_energy_proxy_draining(session: Session) -> None:
    """Long gaps after sessions -> draining."""
    tasks = []
    for i in range(4):
        t = _add_task(session, task_type="exam", raw_hash=f"drain_{i}")
        tasks.append(t)

    # Create sessions with long gaps (>2h) between them
    base_time = datetime.now(UTC) - timedelta(days=7)
    for i, t in enumerate(tasks):
        start = base_time + timedelta(hours=i * 5)  # 5h apart = >2h gap after 1h session
        ws = WorkSession(
            task_id=t.id,
            started_at=start,
            ended_at=start + timedelta(minutes=60),
            duration_minutes=60,
        )
        session.add(ws)
    session.commit()

    patterns = compute_energy_proxy(session)
    # Check stored patterns
    from deadline_agent.awareness.task_affect import get_energy_for_type

    energy = get_energy_for_type(session, "exam")
    assert energy is not None
    assert energy.energy_label == "draining"


def test_energy_proxy_energizing(session: Session) -> None:
    """Short gaps after sessions -> energizing."""
    tasks = []
    for i in range(4):
        t = _add_task(session, task_type="project", raw_hash=f"energy_{i}")
        tasks.append(t)

    # Create sessions with short gaps (<30 min)
    base_time = datetime.now(UTC) - timedelta(days=7)
    for i, t in enumerate(tasks):
        start = base_time + timedelta(minutes=i * 80)  # 80min apart, 60min session = 20min gap
        ws = WorkSession(
            task_id=t.id,
            started_at=start,
            ended_at=start + timedelta(minutes=60),
            duration_minutes=60,
        )
        session.add(ws)
    session.commit()

    compute_energy_proxy(session)

    from deadline_agent.awareness.task_affect import get_energy_for_type

    energy = get_energy_for_type(session, "project")
    assert energy is not None
    assert energy.energy_label == "energizing"


# ── Intervention + helpers ───────────────────────────────────


def test_intervention_text() -> None:
    """Each affect label produces the correct intervention string."""
    assert "10 minutes" in get_intervention("avoidance")
    assert "feels hard" in get_intervention("anxiety")
    assert "enjoy" in get_intervention("enjoyment")
    assert get_intervention("neutral") == ""


def test_get_affect_for_task(session: Session) -> None:
    """get_affect_for_task looks up by normalized type."""
    for i in range(5):
        t = _add_task(session, task_type="project", raw_hash=f"lookup_{i}")
        _add_session(session, t, hours_after_created=10)
    session.commit()

    classify_task_affect(session)

    task = _add_task(session, task_type="project", raw_hash="lookup_target")
    session.commit()
    result = get_affect_for_task(session, task)
    assert result is not None
    assert result.task_type == "project"


# ── State snapshot integration ───────────────────────────────


def test_affect_in_state_snapshot(session: Session) -> None:
    """Affects appear in to_prompt() output."""
    for i in range(5):
        t = _add_task(session, task_type="assignment", raw_hash=f"snap_{i}",
                      hours_until_due=10, created_hours_ago=168)
        _add_session(session, t, hours_after_created=170)
    session.commit()

    classify_task_affect(session)

    from deadline_agent.reasoning.state import StateSnapshot

    snap = StateSnapshot(
        now=datetime.now(UTC),
        task_affect_context=["You avoidance assignments — Start with just 10 minutes to break the seal"],
    )
    prompt = snap.to_prompt()
    assert "TASK AFFECT" in prompt
    assert "avoidance" in prompt
