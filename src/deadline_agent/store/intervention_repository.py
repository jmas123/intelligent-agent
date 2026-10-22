"""Intervention record and escalation repository."""

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from deadline_agent.config import settings
from deadline_agent.models import InterventionEscalation, InterventionRecord

logger = logging.getLogger(__name__)


class InterventionRepository:
    """Thin wrapper around SQLAlchemy for intervention tracking."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- InterventionRecord ---

    def create_record(
        self,
        *,
        task_id: int | None,
        risk_level: str,
        intervention_tier: str,
        category: str,
        title: str,
        body: str,
        fired: bool,
        suppression_reason: str | None = None,
        escalated_from_id: int | None = None,
    ) -> InterventionRecord:
        """Log an intervention decision."""
        record = InterventionRecord(
            task_id=task_id,
            risk_level=risk_level,
            intervention_tier=intervention_tier,
            category=category,
            title=title,
            body=body,
            fired=fired,
            suppression_reason=suppression_reason,
            escalated_from_id=escalated_from_id,
        )
        self._session.add(record)
        self._session.flush()
        return record

    def count_fired_today(self) -> int:
        """Count interventions fired today (UTC day boundary)."""
        today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = (
            select(func.count(InterventionRecord.id))
            .where(InterventionRecord.fired.is_(True))
            .where(InterventionRecord.created_at >= today_start)
        )
        return self._session.execute(stmt).scalar() or 0

    def get_recent_records(self, limit: int = 50) -> list[InterventionRecord]:
        """Get recent intervention records (fired and suppressed), newest first."""
        stmt = (
            select(InterventionRecord)
            .order_by(InterventionRecord.created_at.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt).all())

    def get_last_fired(self) -> InterventionRecord | None:
        """Get the most recently fired intervention."""
        stmt = (
            select(InterventionRecord)
            .where(InterventionRecord.fired.is_(True))
            .order_by(InterventionRecord.created_at.desc())
            .limit(1)
        )
        return self._session.scalars(stmt).first()

    def escalation_chain_depth(self, record_id: int, max_walk: int = 20) -> int:
        """How many escalation hops produced this record (0 = original intervention)."""
        depth = 0
        current = self._session.get(InterventionRecord, record_id)
        while current is not None and current.escalated_from_id is not None and depth < max_walk:
            depth += 1
            current = self._session.get(InterventionRecord, current.escalated_from_id)
        return depth

    def has_recent_record(
        self,
        category: str,
        body_prefix: str,
        within_days: int,
        fired_only: bool = False,
    ) -> bool:
        """Whether a record in this category with this body prefix was logged recently."""
        cutoff = datetime.now(UTC) - timedelta(days=within_days)
        stmt = (
            select(InterventionRecord.id)
            .where(InterventionRecord.category == category)
            .where(InterventionRecord.body.startswith(body_prefix, autoescape=True))
            .where(InterventionRecord.created_at >= cutoff)
            .limit(1)
        )
        if fired_only:
            stmt = stmt.where(InterventionRecord.fired.is_(True))
        return self._session.execute(stmt).first() is not None

    def mark_user_acted(self, task_id: int) -> int:
        """Mark recent fired records for this task as acted upon.

        Returns count of records updated.
        """
        window_start = datetime.now(UTC) - timedelta(
            minutes=settings.intervention_effectiveness_window_minutes
        )
        stmt = (
            select(InterventionRecord)
            .where(InterventionRecord.task_id == task_id)
            .where(InterventionRecord.fired.is_(True))
            .where(InterventionRecord.user_acted.is_(False))
            .where(InterventionRecord.created_at >= window_start)
        )
        records = list(self._session.scalars(stmt).all())
        now = datetime.now(UTC)
        for r in records:
            r.user_acted = True
            r.user_acted_at = now
        self._session.flush()
        return len(records)

    def get_effectiveness_stats(self, days: int = 30) -> dict[str, Any]:
        """Compute effectiveness rates by tier over the past N days."""
        cutoff = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(InterventionRecord)
            .where(InterventionRecord.fired.is_(True))
            .where(InterventionRecord.created_at >= cutoff)
        )
        records = list(self._session.scalars(stmt).all())

        stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "acted": 0})
        for r in records:
            stats[r.intervention_tier]["total"] += 1
            if r.user_acted:
                stats[r.intervention_tier]["acted"] += 1

        return {
            tier: {
                "total": s["total"],
                "acted": s["acted"],
                "rate": round(s["acted"] / s["total"], 2) if s["total"] > 0 else 0.0,
            }
            for tier, s in stats.items()
        }

    # --- InterventionEscalation ---

    def create_escalation(
        self,
        *,
        task_id: int,
        original_record_id: int,
        current_tier: str,
        next_tier: str,
        escalate_after: datetime,
    ) -> InterventionEscalation:
        """Schedule a pending escalation."""
        esc = InterventionEscalation(
            task_id=task_id,
            original_record_id=original_record_id,
            current_tier=current_tier,
            next_tier=next_tier,
            escalate_after=escalate_after,
        )
        self._session.add(esc)
        self._session.flush()
        return esc

    def get_pending_escalations(self) -> list[InterventionEscalation]:
        """Get escalations past their wait time and not cancelled/fired."""
        now = datetime.now(UTC)
        stmt = (
            select(InterventionEscalation)
            .where(InterventionEscalation.cancelled.is_(False))
            .where(InterventionEscalation.fired.is_(False))
            .where(InterventionEscalation.escalate_after <= now)
        )
        return list(self._session.scalars(stmt).all())

    def cancel_escalations_for_task(self, task_id: int, reason: str) -> int:
        """Cancel all pending escalations for a task. Returns count cancelled."""
        stmt = (
            select(InterventionEscalation)
            .where(InterventionEscalation.task_id == task_id)
            .where(InterventionEscalation.cancelled.is_(False))
            .where(InterventionEscalation.fired.is_(False))
        )
        pending = list(self._session.scalars(stmt).all())
        for esc in pending:
            esc.cancelled = True
            esc.cancelled_reason = reason
        self._session.flush()
        return len(pending)

    def mark_escalation_fired(self, escalation_id: int) -> None:
        """Mark an escalation as fired."""
        stmt = select(InterventionEscalation).where(
            InterventionEscalation.id == escalation_id
        )
        esc = self._session.scalars(stmt).first()
        if esc:
            esc.fired = True
            self._session.flush()
