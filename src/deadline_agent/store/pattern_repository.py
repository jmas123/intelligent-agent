"""Repository for BehavioralPattern records."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import BehavioralPattern


class PatternRepository:
    """Thin wrapper around BehavioralPattern queries."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(
        self,
        pattern_type: str,
        pattern_key: str,
        value: str,
        sample_count: int,
        confidence: float,
    ) -> BehavioralPattern:
        """Insert or update a pattern. Returns the upserted record."""
        existing = self.get_one(pattern_type, pattern_key)
        if existing is not None:
            existing.value = value
            existing.sample_count = sample_count
            existing.confidence = confidence
            self._session.commit()
            self._session.refresh(existing)
            return existing

        pattern = BehavioralPattern(
            pattern_type=pattern_type,
            pattern_key=pattern_key,
            value=value,
            sample_count=sample_count,
            confidence=confidence,
        )
        self._session.add(pattern)
        self._session.commit()
        self._session.refresh(pattern)
        return pattern

    def get_all(self) -> list[BehavioralPattern]:
        """Get all patterns, ordered by pattern_type then pattern_key."""
        stmt = select(BehavioralPattern).order_by(
            BehavioralPattern.pattern_type, BehavioralPattern.pattern_key
        )
        return list(self._session.scalars(stmt).all())

    def get_by_type(self, pattern_type: str) -> list[BehavioralPattern]:
        """Get all patterns of a given type."""
        stmt = (
            select(BehavioralPattern)
            .where(BehavioralPattern.pattern_type == pattern_type)
            .order_by(BehavioralPattern.pattern_key)
        )
        return list(self._session.scalars(stmt).all())

    def get_one(self, pattern_type: str, pattern_key: str) -> BehavioralPattern | None:
        """Get a single pattern by type and key."""
        stmt = select(BehavioralPattern).where(
            BehavioralPattern.pattern_type == pattern_type,
            BehavioralPattern.pattern_key == pattern_key,
        )
        return self._session.scalars(stmt).first()
