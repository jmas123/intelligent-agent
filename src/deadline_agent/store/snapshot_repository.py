"""Weekly snapshot repository for persistent weekly summaries."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import WeeklySnapshot


class WeeklySnapshotRepository:
    """Thin wrapper around SQLAlchemy for WeeklySnapshot operations."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, data: dict[str, Any]) -> WeeklySnapshot:
        """Create a new weekly snapshot."""
        snapshot = WeeklySnapshot(**data)
        self._session.add(snapshot)
        self._session.flush()
        self._session.commit()
        return snapshot

    def get_by_week(self, week_start: str) -> WeeklySnapshot | None:
        """Get a snapshot by week start date."""
        stmt = select(WeeklySnapshot).where(
            WeeklySnapshot.week_start == week_start
        )
        return self._session.scalars(stmt).first()

    def get_recent(self, limit: int = 8) -> list[WeeklySnapshot]:
        """List recent snapshots, most recent first."""
        stmt = (
            select(WeeklySnapshot)
            .order_by(WeeklySnapshot.week_start.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt).all())

    def upsert(self, week_start: str, data: dict[str, Any]) -> WeeklySnapshot:
        """Insert or update a snapshot for a given week."""
        existing = self.get_by_week(week_start)
        if existing is None:
            data["week_start"] = week_start
            return self.create(data)
        for key, value in data.items():
            setattr(existing, key, value)
        self._session.commit()
        return existing
