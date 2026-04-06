"""LifeContext repository for CRUD operations."""

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import LifeContext


class LifeContextRepository:
    """Thin wrapper around SQLAlchemy for LifeContext operations."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, data: dict[str, Any]) -> LifeContext:
        """Insert a life context."""
        ctx = LifeContext(**data)
        self._session.add(ctx)
        self._session.flush()
        self._session.commit()
        return ctx

    def get(self, context_id: int) -> LifeContext | None:
        """Get a life context by ID."""
        return self._session.get(LifeContext, context_id)

    def get_active(self, as_of: str | None = None) -> list[LifeContext]:
        """Get all active life contexts whose date range contains as_of.

        Defaults to today if as_of is not provided.
        """
        ref = as_of or date.today().isoformat()
        stmt = (
            select(LifeContext)
            .where(LifeContext.active.is_(True))
            .where(LifeContext.start_date <= ref)
            .where(LifeContext.end_date >= ref)
            .order_by(LifeContext.created_at.desc())
        )
        return list(self._session.scalars(stmt).all())

    def get_by_season(self, season: str) -> list[LifeContext]:
        """Get all contexts for a given season (active or not)."""
        stmt = select(LifeContext).where(LifeContext.season == season)
        return list(self._session.scalars(stmt).all())

    def deactivate(self, context_id: int) -> LifeContext | None:
        """Deactivate a life context. Returns None if not found."""
        ctx = self._session.get(LifeContext, context_id)
        if ctx is None:
            return None
        ctx.active = False
        self._session.commit()
        return ctx

    def set_manual(
        self,
        season: str,
        start_date: str,
        end_date: str,
        label: str | None = None,
    ) -> LifeContext:
        """Create a manual life context, deactivating overlapping auto contexts of same season."""
        # Deactivate overlapping auto-detected contexts of the same season
        stmt = (
            select(LifeContext)
            .where(LifeContext.season == season)
            .where(LifeContext.source == "auto")
            .where(LifeContext.active.is_(True))
            .where(LifeContext.start_date <= end_date)
            .where(LifeContext.end_date >= start_date)
        )
        for existing in self._session.scalars(stmt).all():
            existing.active = False

        # Check for duplicate manual context (same season + dates)
        dup_stmt = (
            select(LifeContext)
            .where(LifeContext.season == season)
            .where(LifeContext.source == "manual")
            .where(LifeContext.start_date == start_date)
            .where(LifeContext.end_date == end_date)
            .where(LifeContext.active.is_(True))
        )
        existing_manual = self._session.scalars(dup_stmt).first()
        if existing_manual is not None:
            if label is not None:
                existing_manual.label = label
            self._session.commit()
            return existing_manual

        ctx = LifeContext(
            season=season,
            label=label,
            start_date=start_date,
            end_date=end_date,
            source="manual",
            active=True,
        )
        self._session.add(ctx)
        self._session.flush()
        self._session.commit()
        return ctx

    def clear_auto(self, season: str | None = None) -> int:
        """Deactivate auto-detected contexts. Optionally filter by season. Returns count."""
        stmt = (
            select(LifeContext)
            .where(LifeContext.source == "auto")
            .where(LifeContext.active.is_(True))
        )
        if season is not None:
            stmt = stmt.where(LifeContext.season == season)
        contexts = list(self._session.scalars(stmt).all())
        for ctx in contexts:
            ctx.active = False
        self._session.commit()
        return len(contexts)

    def has_active_manual(self, season: str, as_of: str | None = None) -> bool:
        """Check if a manual context of this season is active on as_of date."""
        ref = as_of or date.today().isoformat()
        stmt = (
            select(LifeContext)
            .where(LifeContext.season == season)
            .where(LifeContext.source == "manual")
            .where(LifeContext.active.is_(True))
            .where(LifeContext.start_date <= ref)
            .where(LifeContext.end_date >= ref)
        )
        return self._session.scalars(stmt).first() is not None
