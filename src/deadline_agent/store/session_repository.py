"""Repository for WorkSession records."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from deadline_agent.models import WorkSession


class WorkSessionRepository:
    """Thin wrapper around WorkSession queries."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, data: dict[str, Any]) -> WorkSession:
        """Insert a work session record."""
        ws = WorkSession(**data)
        self._session.add(ws)
        self._session.commit()
        self._session.refresh(ws)
        return ws

    def get_for_task(self, task_id: int) -> list[WorkSession]:
        """Get all sessions for a task, ordered by started_at."""
        stmt = (
            select(WorkSession)
            .where(WorkSession.task_id == task_id)
            .order_by(WorkSession.started_at.asc())
        )
        return list(self._session.scalars(stmt).all())

    def get_total_duration(self, task_id: int) -> int:
        """Sum of duration_minutes for a task. Returns 0 if no sessions."""
        result = self._session.execute(
            select(func.coalesce(func.sum(WorkSession.duration_minutes), 0)).where(
                WorkSession.task_id == task_id
            )
        ).scalar()
        return int(result)  # type: ignore[arg-type]

    def get_recent(self, days: int = 30) -> list[WorkSession]:
        """Get sessions from the last N days."""
        cutoff = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(WorkSession)
            .where(WorkSession.started_at >= cutoff)
            .order_by(WorkSession.started_at.desc())
        )
        return list(self._session.scalars(stmt).all())

    def already_inferred(self, task_id: int, started_at: datetime) -> bool:
        """Check if a session already exists for this task at this start time."""
        stmt = (
            select(WorkSession.id)
            .where(WorkSession.task_id == task_id)
            .where(WorkSession.started_at == started_at)
            .limit(1)
        )
        return self._session.execute(stmt).first() is not None
