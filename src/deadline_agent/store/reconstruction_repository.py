"""Daily reconstruction repository."""

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import DailyReconstruction

logger = logging.getLogger(__name__)


class ReconstructionRepository:
    """Thin wrapper around SQLAlchemy for DailyReconstruction operations."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(
        self,
        date: str,
        timeline_json: str,
        overall_confidence: float,
        narrative: str,
    ) -> DailyReconstruction:
        """Insert or update a daily reconstruction."""
        stmt = select(DailyReconstruction).where(DailyReconstruction.date == date)
        existing = self._session.scalars(stmt).first()
        if existing:
            existing.timeline_json = timeline_json
            existing.overall_confidence = overall_confidence
            existing.narrative = narrative
            self._session.flush()
            return existing
        rec = DailyReconstruction(
            date=date,
            timeline_json=timeline_json,
            overall_confidence=overall_confidence,
            narrative=narrative,
        )
        self._session.add(rec)
        self._session.flush()
        return rec

    def get_by_date(self, date: str) -> DailyReconstruction | None:
        """Get reconstruction for a specific date."""
        stmt = select(DailyReconstruction).where(DailyReconstruction.date == date)
        return self._session.scalars(stmt).first()

    def update_segment_confirmation(
        self, date: str, segment_index: int, confirmed: bool
    ) -> bool:
        """Update the confirmed field on a specific timeline segment.

        Returns True if updated, False if not found.
        """
        rec = self.get_by_date(date)
        if not rec:
            return False
        segments = json.loads(rec.timeline_json)
        if segment_index < 0 or segment_index >= len(segments):
            return False
        segments[segment_index]["confirmed"] = confirmed
        rec.timeline_json = json.dumps(segments)

        # Check if all low-confidence segments are now confirmed
        all_confirmed = all(
            seg.get("confirmed") is not None
            for seg in segments
            if seg.get("confidence", 1.0) < 0.6
        )
        if all_confirmed:
            rec.user_confirmed = True

        self._session.flush()
        return True

    def get_recent(self, days: int = 7) -> list[DailyReconstruction]:
        """Get recent reconstructions ordered by date descending."""
        stmt = (
            select(DailyReconstruction)
            .order_by(DailyReconstruction.date.desc())
            .limit(days)
        )
        return list(self._session.scalars(stmt).all())
