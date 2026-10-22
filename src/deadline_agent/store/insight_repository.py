"""Insight repository for CRUD operations."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.config import settings
from deadline_agent.models import Insight


def _age_cutoff(max_age_hours: int) -> datetime:
    """Naive-UTC cutoff matching how created_at is stored (SQL CURRENT_TIMESTAMP)."""
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=max_age_hours)


class InsightRepository:
    """Thin wrapper around SQLAlchemy for Insight operations."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, data: dict[str, Any]) -> Insight:
        """Insert an insight."""
        insight = Insight(**data)
        self._session.add(insight)
        self._session.flush()
        self._session.commit()
        return insight

    def list_active(self, limit: int = 10, max_age_hours: int | None = None) -> list[Insight]:
        """Get active (non-dismissed, non-expired) insights, most recent first.

        Insights are ephemeral observations: anything older than
        ``insight_max_age_hours`` is never surfaced, even if cleanup
        has not run (e.g. the reasoning cycle has been idle).
        """
        if max_age_hours is None:
            max_age_hours = settings.insight_max_age_hours
        stmt = (
            select(Insight)
            .where(Insight.dismissed.is_(False))
            .where(Insight.created_at >= _age_cutoff(max_age_hours))
            .order_by(Insight.created_at.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt).all())

    def dismiss(self, insight_id: int) -> Insight | None:
        """Dismiss an insight. Returns None if not found."""
        insight = self._session.get(Insight, insight_id)
        if insight is None:
            return None
        insight.dismissed = True
        self._session.commit()
        return insight

    def get_for_task(self, task_id: int) -> list[Insight]:
        """Get active insights related to a specific task."""
        stmt = (
            select(Insight)
            .where(Insight.dismissed.is_(False))
            .where(Insight.created_at >= _age_cutoff(settings.insight_max_age_hours))
            .order_by(Insight.created_at.desc())
        )
        insights = list(self._session.scalars(stmt).all())
        return [i for i in insights if task_id in json.loads(i.related_task_ids)]

    def clear_stale(self, max_age_hours: int | None = None) -> int:
        """Delete undismissed insights older than max_age_hours. Returns count deleted.

        Note: BehavioralPattern records are never deleted by this method.
        Patterns are persistent learned behaviors, not ephemeral insights.
        """
        if max_age_hours is None:
            max_age_hours = settings.insight_max_age_hours
        cutoff = _age_cutoff(max_age_hours)
        stmt = (
            select(Insight).where(Insight.dismissed.is_(False)).where(Insight.created_at < cutoff)
        )
        stale = list(self._session.scalars(stmt).all())
        for insight in stale:
            self._session.delete(insight)
        self._session.commit()
        return len(stale)
