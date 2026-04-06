"""Negotiation session repository for multi-turn scheduling conversations."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import NegotiationSession

MAX_HISTORY_MESSAGES = 20


class NegotiationRepository:
    """Thin wrapper around SQLAlchemy for NegotiationSession operations."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, data: dict[str, Any]) -> NegotiationSession:
        """Create a new negotiation session."""
        neg = NegotiationSession(**data)
        self._session.add(neg)
        self._session.flush()
        self._session.commit()
        return neg

    def get(self, session_id: int) -> NegotiationSession | None:
        """Get a session by ID."""
        return self._session.get(NegotiationSession, session_id)

    def list_active(self, limit: int = 10) -> list[NegotiationSession]:
        """Get active negotiation sessions, most recent first."""
        stmt = (
            select(NegotiationSession)
            .where(NegotiationSession.status == "active")
            .order_by(NegotiationSession.created_at.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt).all())

    def add_message(self, session_id: int, role: str, content: str) -> NegotiationSession | None:
        """Append a message to the conversation history. Caps at MAX_HISTORY_MESSAGES."""
        neg = self.get(session_id)
        if neg is None or neg.status != "active":
            return None
        history: list[dict[str, str]] = json.loads(neg.conversation_history)
        history.append(
            {
                "role": role,
                "content": content,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        if len(history) > MAX_HISTORY_MESSAGES:
            history = history[-MAX_HISTORY_MESSAGES:]
        neg.conversation_history = json.dumps(history)
        self._session.commit()
        return neg

    def set_alternatives(
        self, session_id: int, alternatives: list[dict[str, Any]]
    ) -> NegotiationSession | None:
        """Set the proposed alternatives for a session."""
        neg = self.get(session_id)
        if neg is None:
            return None
        neg.proposed_alternatives = json.dumps(alternatives)
        self._session.commit()
        return neg

    def resolve(self, session_id: int, resolution: dict[str, Any]) -> NegotiationSession | None:
        """Mark a session as resolved with the chosen outcome."""
        neg = self.get(session_id)
        if neg is None or neg.status != "active":
            return None
        neg.status = "resolved"
        neg.resolution = json.dumps(resolution)
        self._session.commit()
        return neg

    def abandon(self, session_id: int) -> NegotiationSession | None:
        """Mark a session as abandoned."""
        neg = self.get(session_id)
        if neg is None or neg.status != "active":
            return None
        neg.status = "abandoned"
        self._session.commit()
        return neg

    def expire_stale(self, hours: int = 24) -> int:
        """Abandon sessions with no activity for N hours. Returns count expired."""
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        stmt = (
            select(NegotiationSession)
            .where(NegotiationSession.status == "active")
            .where(NegotiationSession.updated_at < cutoff)
        )
        stale = list(self._session.scalars(stmt).all())
        for neg in stale:
            neg.status = "abandoned"
        self._session.commit()
        return len(stale)
