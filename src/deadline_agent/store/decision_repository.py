"""Repository for Decision model CRUD operations."""

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import Decision


class DecisionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        description: str,
        chosen_option: str,
        alternatives: list[str] | None = None,
        context: dict | None = None,
    ) -> Decision:
        decision = Decision(
            description=description,
            chosen_option=chosen_option,
            alternatives_considered=json.dumps(alternatives or []),
            context_json=json.dumps(context or {}),
        )
        self._session.add(decision)
        self._session.commit()
        return decision

    def list_recent(self, limit: int = 10) -> list[Decision]:
        return list(
            self._session.scalars(
                select(Decision).order_by(Decision.created_at.desc()).limit(limit)
            ).all()
        )

    def get(self, decision_id: int) -> Decision | None:
        return self._session.get(Decision, decision_id)

    def record_outcome(self, decision_id: int, outcome: str) -> Decision | None:
        decision = self.get(decision_id)
        if decision is None:
            return None
        decision.outcome = outcome
        decision.outcome_recorded_at = datetime.now(UTC)
        self._session.commit()
        return decision

    def list_with_outcomes(self, limit: int = 5) -> list[Decision]:
        """Get recent decisions that have recorded outcomes."""
        return list(
            self._session.scalars(
                select(Decision)
                .where(Decision.outcome.is_not(None))
                .order_by(Decision.created_at.desc())
                .limit(limit)
            ).all()
        )
