"""Context switch detection: surface relevant info when life_track changes."""

import logging
from typing import Any

from deadline_agent.events import CONTEXT_SWITCH_DETECTED, Event, EventBus
from deadline_agent.notifications.channels import (
    Notification,
    NotificationPriority,
    notification_router,
)

logger = logging.getLogger(__name__)


class ContextSwitchHandler:
    """Responds to life_track transitions by surfacing relevant context."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def register(self, bus: EventBus) -> None:
        """Subscribe to context switch events."""
        bus.subscribe(CONTEXT_SWITCH_DETECTED, self._on_context_switch)

    async def _on_context_switch(self, event: Event) -> None:
        """Handle a life_track transition."""
        from_mode = event.payload.get("from_mode", "")
        to_mode = event.payload.get("to_mode", "")

        logger.info("Context switch: %s → %s", from_mode, to_mode)

        try:
            with self._session_factory() as session:
                if to_mode == "recruiting":
                    summary = self._recruiting_summary(session)
                elif to_mode in ("school", None):
                    summary = self._school_summary(session)
                elif to_mode == "project":
                    summary = self._project_summary(session)
                else:
                    return

                if summary:
                    await notification_router.send(
                        Notification(
                            title=f"Switched to {to_mode}",
                            body=summary,
                            priority=NotificationPriority.AMBIENT,
                            category=to_mode or "school",
                        )
                    )
        except Exception:
            logger.exception("Context switch handler error")

    def _recruiting_summary(self, session: Any) -> str:
        """Surface recruiting context on switch to recruiting mode."""
        from datetime import UTC, datetime

        from sqlalchemy import select

        from deadline_agent.models import RecruitingApplication, Task

        # Active applications needing attention
        from deadline_agent.store.recruiting_repository import RecruitingRepository

        repo = RecruitingRepository(session)
        active = repo.list_active()
        now = datetime.now(UTC)

        stale = [
            a
            for a in active
            if a.last_signal_at
            and (now - a.last_signal_at.replace(tzinfo=UTC if a.last_signal_at.tzinfo is None else a.last_signal_at.tzinfo)).days >= 7
        ]

        # Upcoming interviews
        interviews = list(
            session.scalars(
                select(Task)
                .where(Task.status == "pending")
                .where(Task.type == "interview_prep")
                .where(Task.due_date_iso >= now.isoformat())
                .order_by(Task.due_date_iso.asc())
                .limit(3)
            ).all()
        )

        parts: list[str] = []
        if interviews:
            parts.append(f"{len(interviews)} upcoming interview(s)")
        if stale:
            names = ", ".join(a.company_name for a in stale[:3])
            parts.append(f"{len(stale)} app(s) need follow-up ({names})")
        if active and not interviews and not stale:
            parts.append(f"{len(active)} active application(s)")

        return ". ".join(parts) if parts else ""

    def _school_summary(self, session: Any) -> str:
        """Surface academic context on switch to school mode."""
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import select

        from deadline_agent.models import Task

        now = datetime.now(UTC)
        end_of_today = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        overdue = session.execute(
            select(Task)
            .where(Task.status == "pending")
            .where(Task.due_date_iso < now.isoformat())
            .where(Task.due_date_iso.is_not(None))
        ).scalars().all()

        due_today = session.execute(
            select(Task)
            .where(Task.status == "pending")
            .where(Task.due_date_iso >= now.isoformat())
            .where(Task.due_date_iso < end_of_today.isoformat())
        ).scalars().all()

        parts: list[str] = []
        if overdue:
            parts.append(f"{len(list(overdue))} overdue task(s)")
        if due_today:
            parts.append(f"{len(list(due_today))} due today")

        return ". ".join(parts) if parts else ""

    def _project_summary(self, session: Any) -> str:
        """Surface project context on switch to project mode."""
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import select

        from deadline_agent.models import Task

        now = datetime.now(UTC)
        week_ahead = (now + timedelta(days=7)).isoformat()

        pending = list(
            session.scalars(
                select(Task)
                .where(Task.status == "pending")
                .where(Task.type.in_(["assignment", "meeting"]))
                .where(Task.due_date_iso.is_not(None))
                .where(Task.due_date_iso <= week_ahead)
                .order_by(Task.due_date_iso.asc())
                .limit(3)
            ).all()
        )

        if pending:
            titles = ", ".join(t.title for t in pending[:3])
            return f"{len(pending)} task(s) due this week: {titles}"
        return ""
