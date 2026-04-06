"""Proposed action repository for CRUD operations."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import ProposedAction


class ActionRepository:
    """Thin wrapper around SQLAlchemy for ProposedAction operations."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def propose(self, data: dict[str, Any]) -> ProposedAction:
        """Create a new proposed action."""
        action = ProposedAction(**data)
        self._session.add(action)
        self._session.flush()
        self._session.commit()
        return action

    def list_pending(self, limit: int = 20) -> list[ProposedAction]:
        """Get pending (proposed) actions, most recent first."""
        stmt = (
            select(ProposedAction)
            .where(ProposedAction.status == "proposed")
            .order_by(ProposedAction.created_at.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt).all())

    def get(self, action_id: int) -> ProposedAction | None:
        """Get an action by ID."""
        return self._session.get(ProposedAction, action_id)

    def approve(self, action_id: int) -> ProposedAction | None:
        """Mark an action as approved. Returns None if not found or not pending."""
        action = self.get(action_id)
        if action is None or action.status != "proposed":
            return None
        action.status = "approved"
        action.approved_at = datetime.now(UTC)
        self._session.commit()
        return action

    def reject(self, action_id: int) -> ProposedAction | None:
        """Mark an action as rejected. Returns None if not found or not pending."""
        action = self.get(action_id)
        if action is None or action.status != "proposed":
            return None
        action.status = "rejected"
        self._session.commit()
        return action

    def mark_executed(self, action_id: int, error: str | None = None) -> ProposedAction | None:
        """Mark an action as executed (or failed)."""
        action = self.get(action_id)
        if action is None:
            return None
        if error:
            action.execution_error = error
        else:
            action.status = "executed"
            action.executed_at = datetime.now(UTC)
        self._session.commit()
        return action

    def expire_stale(self, hours: int = 24) -> int:
        """Expire proposed actions older than N hours. Returns count expired."""
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        stmt = (
            select(ProposedAction)
            .where(ProposedAction.status == "proposed")
            .where(ProposedAction.created_at < cutoff)
        )
        stale = list(self._session.scalars(stmt).all())
        for action in stale:
            action.status = "expired"
        self._session.commit()
        return len(stale)
