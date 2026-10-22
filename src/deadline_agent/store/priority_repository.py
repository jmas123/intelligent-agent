"""Priority ranking repository for CRUD operations."""

from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from deadline_agent.models import PriorityRanking, Task


class PriorityRepository:
    """Thin wrapper around SQLAlchemy for PriorityRanking operations."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, task_id: int, data: dict[str, Any]) -> PriorityRanking:
        """Insert or update a priority ranking for a task."""
        stmt = select(PriorityRanking).where(PriorityRanking.task_id == task_id)
        existing = self._session.scalars(stmt).first()
        if existing:
            for key, value in data.items():
                setattr(existing, key, value)
            existing.computed_at = datetime.now()
            self._session.flush()
            return existing
        ranking = PriorityRanking(task_id=task_id, **data)
        self._session.add(ranking)
        self._session.flush()
        return ranking

    def get_ranked(self, limit: int = 10) -> list[tuple[PriorityRanking, Task]]:
        """Get priority rankings joined with tasks, ordered by rank."""
        stmt = (
            select(PriorityRanking, Task)
            .join(Task, PriorityRanking.task_id == Task.id)
            .where(Task.status == "pending")
            .order_by(PriorityRanking.rank.asc())
            .limit(limit)
        )
        return list(self._session.execute(stmt).all())

    def get_for_task(self, task_id: int) -> PriorityRanking | None:
        """Get priority ranking for a specific task."""
        stmt = select(PriorityRanking).where(PriorityRanking.task_id == task_id)
        return self._session.scalars(stmt).first()

    def clear_all(self) -> int:
        """Remove all priority rankings. Returns count deleted."""
        stmt = delete(PriorityRanking)
        result = self._session.execute(stmt)
        self._session.flush()
        return result.rowcount  # type: ignore[return-value]

    def remove_for_task(self, task_id: int) -> None:
        """Remove priority ranking for a completed/dismissed task."""
        stmt = delete(PriorityRanking).where(PriorityRanking.task_id == task_id)
        self._session.execute(stmt)
        self._session.flush()
