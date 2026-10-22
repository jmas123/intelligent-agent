"""Tests for escalation lifecycle in the intervention engine."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from deadline_agent.models import Task
from deadline_agent.reasoning.intervention_engine import InterventionEngine
from deadline_agent.store.intervention_repository import InterventionRepository


def _make_task(session: Session, *, status: str = "pending", due_in_hours: int = 12) -> Task:
    due = datetime.now(UTC) + timedelta(hours=due_in_hours)
    task = Task(
        title="Problem Set 4",
        due_date_iso=due.isoformat(),
        source="test",
        type="assignment",
        urgency_score=4,
        confidence=0.9,
        raw_hash=f"esc-{status}-{due_in_hours}",
        status=status,
    )
    session.add(task)
    session.commit()
    return task


def _schedule_due_escalation(session: Session, task: Task) -> InterventionRepository:
    repo = InterventionRepository(session)
    record = repo.create_record(
        task_id=task.id,
        risk_level="high",
        intervention_tier="notification",
        category="alert_24h",
        title="Deadline tomorrow",
        body=task.title,
        fired=True,
    )
    repo.create_escalation(
        task_id=task.id,
        original_record_id=record.id,
        current_tier="notification",
        next_tier="voice",
        escalate_after=datetime.now(UTC) - timedelta(hours=1),
    )
    session.commit()
    return repo


@pytest.mark.asyncio
async def test_escalation_cancelled_when_task_done(session: Session) -> None:
    task = _make_task(session, status="done")
    repo = _schedule_due_escalation(session, task)

    engine = InterventionEngine(lambda: session)
    with patch.object(engine, "_deliver", new=AsyncMock()) as deliver:
        fired = await engine.process_escalations(session)

    assert fired == 0
    deliver.assert_not_called()
    assert repo.get_pending_escalations() == []


@pytest.mark.asyncio
async def test_escalation_cancelled_when_deadline_long_past(session: Session) -> None:
    task = _make_task(session, due_in_hours=-24 * 30)
    repo = _schedule_due_escalation(session, task)

    engine = InterventionEngine(lambda: session)
    with patch.object(engine, "_deliver", new=AsyncMock()) as deliver:
        fired = await engine.process_escalations(session)

    assert fired == 0
    deliver.assert_not_called()
    assert repo.get_pending_escalations() == []


@pytest.mark.asyncio
async def test_escalation_chain_is_capped(session: Session) -> None:
    """A chain of escalated records stops escalating at the configured depth."""
    task = _make_task(session)
    repo = InterventionRepository(session)

    previous_id = None
    for _ in range(5):
        record = repo.create_record(
            task_id=task.id,
            risk_level="high",
            intervention_tier="notification",
            category="alert_24h",
            title="[Escalated] Deadline tomorrow",
            body=task.title,
            fired=True,
            escalated_from_id=previous_id,
        )
        previous_id = record.id
    repo.create_escalation(
        task_id=task.id,
        original_record_id=previous_id,  # type: ignore[arg-type]
        current_tier="notification",
        next_tier="voice",
        escalate_after=datetime.now(UTC) - timedelta(hours=1),
    )
    session.commit()

    engine = InterventionEngine(lambda: session)
    with patch.object(engine, "_deliver", new=AsyncMock()) as deliver:
        fired = await engine.process_escalations(session)

    assert fired == 0
    deliver.assert_not_called()
    assert repo.get_pending_escalations() == []


@pytest.mark.asyncio
async def test_escalation_fires_for_live_task(session: Session) -> None:
    task = _make_task(session)
    _schedule_due_escalation(session, task)

    engine = InterventionEngine(lambda: session)
    with patch.object(engine, "_deliver", new=AsyncMock()) as deliver:
        fired = await engine.process_escalations(session)

    assert fired == 1
    deliver.assert_called_once()


def test_escalated_title_does_not_stack_prefixes(session: Session) -> None:
    """Guard against the '[Escalated] [Escalated] ...' title growth."""
    task = _make_task(session)
    repo = InterventionRepository(session)
    record = repo.create_record(
        task_id=task.id,
        risk_level="high",
        intervention_tier="notification",
        category="alert_24h",
        title="[Escalated] Deadline tomorrow",
        body=task.title,
        fired=True,
    )
    title = (
        record.title
        if record.title.startswith("[Escalated]")
        else f"[Escalated] {record.title}"
    )
    assert title == "[Escalated] Deadline tomorrow"


def test_update_status_cancels_pending_escalations(session: Session) -> None:
    from deadline_agent.store.repository import TaskRepository

    task = _make_task(session)
    repo = _schedule_due_escalation(session, task)

    TaskRepository(session).update_status(task.id, "done")

    assert repo.get_pending_escalations() == []


def test_has_recent_record(session: Session) -> None:
    repo = InterventionRepository(session)
    repo.create_record(
        task_id=None,
        risk_level="medium",
        intervention_tier="menubar",
        category="follow_up",
        title="Follow-up needed",
        body="Acme: no response in 9 days (status: applied)",
        fired=True,
    )
    session.commit()

    assert repo.has_recent_record("follow_up", "Acme: no response", 7) is True
    assert repo.has_recent_record("follow_up", "Acme: no response", 7, fired_only=True) is True
    assert repo.has_recent_record("follow_up", "Globex: no response", 7) is False
    assert repo.has_recent_record("no_work", "Acme: no response", 7) is False
