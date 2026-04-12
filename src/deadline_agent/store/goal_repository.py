"""Repository for Goal model CRUD operations."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import Goal


class GoalRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        description: str,
        category: str,
        target_metric: str | None = None,
    ) -> Goal:
        goal = Goal(
            description=description,
            category=category,
            target_metric=target_metric,
        )
        self._session.add(goal)
        self._session.commit()
        return goal

    def list_active(self) -> list[Goal]:
        return list(
            self._session.scalars(
                select(Goal)
                .where(Goal.status == "active")
                .order_by(Goal.created_at.desc())
            ).all()
        )

    def list_all(self, limit: int = 20) -> list[Goal]:
        return list(
            self._session.scalars(
                select(Goal).order_by(Goal.created_at.desc()).limit(limit)
            ).all()
        )

    def get(self, goal_id: int) -> Goal | None:
        return self._session.get(Goal, goal_id)

    def update_status(self, goal_id: int, status: str) -> Goal | None:
        goal = self.get(goal_id)
        if goal is None:
            return None
        goal.status = status
        goal.updated_at = datetime.now(UTC)
        self._session.commit()
        return goal
