"""Repository for Relationship model CRUD operations."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import Relationship


class RelationshipRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(
        self,
        person: str,
        channel: str,
        interaction_at: datetime | None = None,
    ) -> Relationship:
        """Find or create a relationship, incrementing interaction count."""
        normalized = person.strip().lower()
        rel = self._session.scalar(
            select(Relationship)
            .where(Relationship.person_normalized == normalized)
            .where(Relationship.channel == channel)
        )

        if rel is None:
            rel = Relationship(
                person=person,
                person_normalized=normalized,
                channel=channel,
                interaction_count=1,
                last_interaction_at=interaction_at or datetime.now(UTC),
            )
            self._session.add(rel)
        else:
            rel.interaction_count += 1
            if interaction_at and (
                rel.last_interaction_at is None
                or interaction_at.replace(tzinfo=None) > rel.last_interaction_at.replace(tzinfo=None)
            ):
                rel.last_interaction_at = interaction_at
            rel.updated_at = datetime.now(UTC)

        self._session.commit()
        return rel

    def update_trend(self, rel_id: int, trend: str) -> Relationship | None:
        rel = self._session.get(Relationship, rel_id)
        if rel is None:
            return None
        rel.trend = trend
        rel.updated_at = datetime.now(UTC)
        self._session.commit()
        return rel

    def list_all(self, limit: int = 50) -> list[Relationship]:
        return list(
            self._session.scalars(
                select(Relationship)
                .order_by(Relationship.last_interaction_at.desc())
                .limit(limit)
            ).all()
        )

    def list_atrophying(self) -> list[Relationship]:
        return list(
            self._session.scalars(
                select(Relationship).where(Relationship.trend == "atrophying")
            ).all()
        )

    def list_stale(self, days: int = 14) -> list[Relationship]:
        """Relationships with no interaction in N days."""
        cutoff = datetime.now(UTC) - timedelta(days=days)
        return list(
            self._session.scalars(
                select(Relationship)
                .where(Relationship.last_interaction_at < cutoff)
                .order_by(Relationship.last_interaction_at.asc())
            ).all()
        )

    def get_by_person(self, person: str, channel: str) -> Relationship | None:
        normalized = person.strip().lower()
        return self._session.scalar(
            select(Relationship)
            .where(Relationship.person_normalized == normalized)
            .where(Relationship.channel == channel)
        )

    def search_by_name_or_email(self, query: str) -> list[Relationship]:
        """Find relationships matching a name or email (case-insensitive partial match)."""
        normalized = query.strip().lower()
        if not normalized:
            return []
        return list(
            self._session.scalars(
                select(Relationship)
                .where(
                    (Relationship.person_normalized.contains(normalized))
                    | (Relationship.person.ilike(f"%{normalized}%"))
                )
                .order_by(Relationship.last_interaction_at.desc())
                .limit(5)
            ).all()
        )
