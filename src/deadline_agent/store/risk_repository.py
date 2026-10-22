"""Task risk repository for CRUD operations."""

from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from deadline_agent.models import Task, TaskRisk


class RiskRepository:
    """Thin wrapper around SQLAlchemy for TaskRisk operations."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, task_id: int, data: dict[str, Any]) -> TaskRisk:
        """Insert or update a risk assessment for a task."""
        stmt = select(TaskRisk).where(TaskRisk.task_id == task_id)
        existing = self._session.scalars(stmt).first()
        if existing:
            for key, value in data.items():
                setattr(existing, key, value)
            existing.computed_at = datetime.now()
            self._session.flush()
            return existing
        risk = TaskRisk(task_id=task_id, **data)
        self._session.add(risk)
        self._session.flush()
        return risk

    def get_all_active(self) -> list[tuple[TaskRisk, Task]]:
        """Get all risk assessments joined with pending tasks, ordered by P(miss) descending."""
        stmt = (
            select(TaskRisk, Task)
            .join(Task, TaskRisk.task_id == Task.id)
            .where(Task.status == "pending")
            .order_by(TaskRisk.probability_of_missing.desc())
        )
        return list(self._session.execute(stmt).all())

    def get_for_task(self, task_id: int) -> TaskRisk | None:
        """Get risk assessment for a specific task."""
        stmt = select(TaskRisk).where(TaskRisk.task_id == task_id)
        return self._session.scalars(stmt).first()

    def get_high_risk(self, threshold: float = 0.6) -> list[tuple[TaskRisk, Task]]:
        """Get tasks with P(miss) above threshold."""
        stmt = (
            select(TaskRisk, Task)
            .join(Task, TaskRisk.task_id == Task.id)
            .where(Task.status == "pending")
            .where(TaskRisk.probability_of_missing >= threshold)
            .order_by(TaskRisk.probability_of_missing.desc())
        )
        return list(self._session.execute(stmt).all())

    def remove_for_task(self, task_id: int) -> None:
        """Remove risk assessment for a completed/dismissed task."""
        stmt = delete(TaskRisk).where(TaskRisk.task_id == task_id)
        self._session.execute(stmt)
        self._session.flush()
