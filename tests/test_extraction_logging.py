"""Tests for extraction I/O logging and dismiss feedback propagation."""

from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import ExtractionLog, Task
from deadline_agent.store.repository import TaskRepository


def _make_task_with_log(session: Session) -> tuple[Task, ExtractionLog]:
    """Create a task and its linked extraction log."""
    task = Task(
        title="HW5",
        due_date_iso="2026-04-10T23:59:00",
        source="gmail",
        type="assignment",
        course="CS101",
        urgency_score=3,
        confidence=0.9,
        raw_hash="test_hash_123",
        status="pending",
    )
    session.add(task)
    session.flush()

    log = ExtractionLog(
        input_text="Subject: HW5\nContent: Due Friday",
        output_json='{"title":"HW5"}',
        model_used="llama3.2:3b",
        confidence=0.9,
        task_id=task.id,
        accepted=True,
    )
    session.add(log)
    session.flush()
    return task, log


class TestDismissFeedback:
    def test_dismiss_flips_accepted_to_false(self, session: Session) -> None:
        task, log = _make_task_with_log(session)
        assert log.accepted is True

        repo = TaskRepository(session)
        repo.update_status(task.id, "dismissed")

        # Refresh from DB
        updated_log = session.get(ExtractionLog, log.id)
        assert updated_log is not None
        assert updated_log.accepted is False

    def test_done_does_not_flip_accepted(self, session: Session) -> None:
        task, log = _make_task_with_log(session)

        repo = TaskRepository(session)
        repo.update_status(task.id, "done")

        updated_log = session.get(ExtractionLog, log.id)
        assert updated_log is not None
        assert updated_log.accepted is True

    def test_dismiss_with_no_log_does_not_error(self, session: Session) -> None:
        task = Task(
            title="Orphan",
            source="gmail",
            type="assignment",
            urgency_score=2,
            confidence=0.8,
            raw_hash="orphan_hash",
            status="pending",
        )
        session.add(task)
        session.flush()

        repo = TaskRepository(session)
        result = repo.update_status(task.id, "dismissed")
        assert result is not None
        assert result.status == "dismissed"


class TestExtractionLogModel:
    def test_defaults(self, session: Session) -> None:
        log = ExtractionLog(
            input_text="test input",
            output_json="{}",
            model_used="test-model",
            confidence=0.5,
        )
        session.add(log)
        session.flush()

        assert log.id is not None
        assert log.accepted is True
        assert log.task_id is None
        assert log.created_at is not None

    def test_query_by_accepted(self, session: Session) -> None:
        session.add(ExtractionLog(
            input_text="a", output_json="{}", model_used="m", confidence=0.9, accepted=True,
        ))
        session.add(ExtractionLog(
            input_text="b", output_json="{}", model_used="m", confidence=0.9, accepted=False,
        ))
        session.flush()

        accepted = session.scalars(
            select(ExtractionLog).where(ExtractionLog.accepted.is_(True))
        ).all()
        assert len(accepted) == 1
        assert accepted[0].input_text == "a"
