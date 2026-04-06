"""File activity repository for CRUD operations."""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from deadline_agent.models import FileActivity, FileTaskLink


class FileActivityRepository:
    """Thin wrapper around SQLAlchemy for FileActivity and FileTaskLink operations."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_activity(self, data: dict[str, Any]) -> FileActivity:
        """Insert a file activity record."""
        activity = FileActivity(**data)
        self._session.add(activity)
        self._session.flush()
        self._session.commit()
        return activity

    def link_to_task(
        self, file_activity_id: int, task_id: int, confidence: float, method: str
    ) -> FileTaskLink | None:
        """Link a file activity to a task. Returns None if link already exists."""
        link = FileTaskLink(
            file_activity_id=file_activity_id,
            task_id=task_id,
            confidence=confidence,
            method=method,
        )
        self._session.add(link)
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            return None
        self._session.commit()
        return link

    def get_activity_for_task(
        self, task_id: int, since: datetime | None = None
    ) -> list[FileActivity]:
        """Get all file activity linked to a task, optionally since a datetime."""
        stmt = (
            select(FileActivity)
            .join(FileTaskLink, FileTaskLink.file_activity_id == FileActivity.id)
            .where(FileTaskLink.task_id == task_id)
            .order_by(FileActivity.modified_at.desc())
        )
        if since is not None:
            stmt = stmt.where(FileActivity.created_at >= since)
        return list(self._session.scalars(stmt).all())

    def get_recent_activity(self, hours: int = 72) -> list[FileActivity]:
        """Get all file activity from the last N hours."""
        cutoff = datetime.now() - timedelta(hours=hours)
        stmt = (
            select(FileActivity)
            .where(FileActivity.created_at >= cutoff)
            .order_by(FileActivity.modified_at.desc())
        )
        return list(self._session.scalars(stmt).all())

    def has_activity_for_task(self, task_id: int, since_hours: int = 72) -> bool:
        """Check if any file activity is linked to a task within the time window."""
        cutoff = datetime.now() - timedelta(hours=since_hours)
        stmt = (
            select(FileTaskLink.id)
            .join(FileActivity, FileTaskLink.file_activity_id == FileActivity.id)
            .where(FileTaskLink.task_id == task_id)
            .where(FileActivity.created_at >= cutoff)
            .limit(1)
        )
        return self._session.execute(stmt).first() is not None

    def get_links_for_task(self, task_id: int) -> list[FileTaskLink]:
        """Get all file-task links for a task."""
        stmt = (
            select(FileTaskLink)
            .where(FileTaskLink.task_id == task_id)
            .order_by(FileTaskLink.confidence.desc())
        )
        return list(self._session.scalars(stmt).all())
