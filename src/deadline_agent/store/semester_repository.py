"""Semester record repository for debrief snapshots."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import SemesterRecord


class SemesterRecordRepository:
    """Thin wrapper around SQLAlchemy for SemesterRecord operations."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, data: dict[str, Any]) -> SemesterRecord:
        """Create a new semester record."""
        record = SemesterRecord(**data)
        self._session.add(record)
        self._session.flush()
        self._session.commit()
        return record

    def get(self, record_id: int) -> SemesterRecord | None:
        """Get a record by ID."""
        return self._session.get(SemesterRecord, record_id)

    def get_by_term(self, term_name: str) -> SemesterRecord | None:
        """Get a record by term name."""
        stmt = select(SemesterRecord).where(
            SemesterRecord.term_name == term_name
        )
        return self._session.scalars(stmt).first()

    def list_all(self, limit: int = 20) -> list[SemesterRecord]:
        """List all semester records, most recent first."""
        stmt = (
            select(SemesterRecord)
            .order_by(SemesterRecord.created_at.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt).all())

    def update(
        self, record_id: int, data: dict[str, Any]
    ) -> SemesterRecord | None:
        """Update an existing semester record."""
        record = self.get(record_id)
        if record is None:
            return None
        for key, value in data.items():
            setattr(record, key, value)
        self._session.commit()
        return record
