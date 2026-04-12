"""Proactive interrupts: surface pending context when a work session starts."""

import logging
from typing import Any

from deadline_agent.events import SESSION_STARTED, Event, EventBus
from deadline_agent.notifications.channels import (
    Notification,
    NotificationPriority,
    notification_router,
)

logger = logging.getLogger(__name__)


class ProactiveInterruptHandler:
    """Surfaces relevant pending items when the user starts a work session.

    Triggered by SESSION_STARTED events (first file activity after idle).
    Generates a contextual "before you start" notification with overdue tasks,
    stale follow-ups, and time estimate.
    """

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def register(self, bus: EventBus) -> None:
        """Subscribe to session start events."""
        bus.subscribe(SESSION_STARTED, self._on_session_started)

    async def _on_session_started(self, event: Event) -> None:
        """Generate a proactive interrupt when user starts a work session."""
        life_track = event.payload.get("life_track")

        try:
            with self._session_factory() as session:
                context = self._build_interrupt_context(session, life_track)
                if context:
                    await notification_router.send(
                        Notification(
                            title="Before you start",
                            body=context,
                            priority=NotificationPriority.LOW,
                            category="session",
                        )
                    )
                    logger.info("Proactive interrupt sent: %s", context[:80])
        except Exception:
            logger.exception("Proactive interrupt error")

    def _build_interrupt_context(
        self, session: Any, life_track: str | None
    ) -> str | None:
        """Check for pending items that need attention before diving into work."""
        from datetime import UTC, datetime

        from sqlalchemy import func, select

        from deadline_agent.models import Task

        now = datetime.now(UTC)
        items: list[str] = []

        # Overdue tasks
        overdue_count = (
            session.execute(
                select(func.count(Task.id))
                .where(Task.status == "pending")
                .where(Task.due_date_iso < now.isoformat())
                .where(Task.due_date_iso.is_not(None))
            ).scalar()
            or 0
        )
        if overdue_count > 0:
            items.append(f"{overdue_count} overdue task(s)")

        # Stale recruiting follow-ups (if in recruiting mode or general)
        if life_track in ("recruiting", None):
            try:
                from deadline_agent.store.recruiting_repository import (
                    RecruitingRepository,
                )

                repo = RecruitingRepository(session)
                active = repo.list_active()
                stale = [
                    a
                    for a in active
                    if a.last_signal_at
                    and (
                        now
                        - a.last_signal_at.replace(
                            tzinfo=UTC
                            if a.last_signal_at.tzinfo is None
                            else a.last_signal_at.tzinfo
                        )
                    ).days
                    >= 7
                ]
                if stale:
                    company = stale[0].company_name
                    days = (
                        now
                        - stale[0].last_signal_at.replace(
                            tzinfo=UTC
                            if stale[0].last_signal_at.tzinfo is None
                            else stale[0].last_signal_at.tzinfo
                        )
                    ).days
                    items.append(f"{company} email unread ({days}d)")
            except Exception:
                pass

        # Due-today tasks
        from datetime import timedelta
        from zoneinfo import ZoneInfo

        local_now = now.astimezone(ZoneInfo("America/New_York"))
        local_eod = (local_now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end_of_today = local_eod.astimezone(UTC)
        due_today_count = (
            session.execute(
                select(func.count(Task.id))
                .where(Task.status == "pending")
                .where(Task.due_date_iso >= now.isoformat())
                .where(Task.due_date_iso < end_of_today.isoformat())
            ).scalar()
            or 0
        )
        if due_today_count > 0:
            items.append(f"{due_today_count} due today")

        if not items:
            return None

        # Estimate time
        total_items = len(items)
        est_minutes = total_items * 5  # rough estimate
        summary = ", ".join(items)
        return f"{summary} — ~{est_minutes} min to review"
