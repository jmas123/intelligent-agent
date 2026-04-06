"""Task repository for CRUD operations."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from deadline_agent.models import Task


class TaskRepository:
    """Thin wrapper around SQLAlchemy for Task operations."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_task(self, data: dict[str, Any]) -> Task | None:
        """Insert a task. Returns None if raw_hash already exists (duplicate)."""
        task = Task(**data)
        self._session.add(task)
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            return None
        self._session.commit()
        return task

    def exists_by_hash(self, raw_hash: str) -> bool:
        """Check if a task with this raw_hash already exists."""
        stmt = select(Task.id).where(Task.raw_hash == raw_hash)
        return self._session.execute(stmt).first() is not None

    def list_tasks(
        self,
        status: str | None = None,
        source: str | None = None,
        limit: int = 50,
    ) -> list[Task]:
        """Query tasks with optional filters, ordered by due_date_iso ASC."""
        stmt = select(Task).order_by(Task.due_date_iso.asc())
        if status is not None:
            stmt = stmt.where(Task.status == status)
        if source is not None:
            stmt = stmt.where(Task.source == source)
        stmt = stmt.limit(limit)
        return list(self._session.scalars(stmt).all())

    def get_task(self, task_id: int) -> Task | None:
        """Get a single task by ID."""
        return self._session.get(Task, task_id)

    def update_status(self, task_id: int, status: str) -> Task | None:
        """Update a task's status. Returns None if not found."""
        task = self.get_task(task_id)
        if task is None:
            return None
        task.status = status
        self._session.commit()
        return task

    def list_tasks_due_today(self) -> list[Task]:
        """Return pending tasks due today."""
        now = datetime.now(UTC)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        end = (
            now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        ).isoformat()
        stmt = (
            select(Task)
            .where(Task.status == "pending")
            .where(Task.due_date_iso >= start)
            .where(Task.due_date_iso < end)
            .order_by(Task.due_date_iso.asc())
        )
        return list(self._session.scalars(stmt).all())

    def list_overdue_tasks(self) -> list[Task]:
        """Return pending tasks past their due date."""
        now_iso = datetime.now(UTC).isoformat()
        stmt = (
            select(Task)
            .where(Task.status == "pending")
            .where(Task.due_date_iso < now_iso)
            .where(Task.due_date_iso.is_not(None))
            .order_by(Task.due_date_iso.asc())
        )
        return list(self._session.scalars(stmt).all())

    def count_by_status(self) -> dict[str, int]:
        """Return task counts grouped by status."""
        counts: dict[str, int] = {"pending": 0, "done": 0, "dismissed": 0}
        rows = self._session.execute(
            select(Task.status, func.count(Task.id)).group_by(Task.status)
        ).all()
        for status, count in rows:
            if status in counts:
                counts[status] = count
        return counts
