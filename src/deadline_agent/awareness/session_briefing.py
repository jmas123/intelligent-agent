"""Session briefing: auto-generate a concise briefing when work starts."""

import logging
from typing import Any

from deadline_agent.events import SESSION_STARTED, Event, EventBus
from deadline_agent.notifications.channels import (
    Notification,
    NotificationPriority,
    notification_router,
)

logger = logging.getLogger(__name__)


class SessionBriefingGenerator:
    """Generates a 30-second briefing when the user sits down to work.

    Subscribes to SESSION_STARTED events and produces a concise summary
    of what matters right now. Only fires once per idle-to-active transition.
    """

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory
        self._last_briefing_session_ts: str | None = None

    def register(self, bus: EventBus) -> None:
        """Subscribe to session start events."""
        bus.subscribe(SESSION_STARTED, self._on_session_started)

    async def _on_session_started(self, event: Event) -> None:
        """Generate and deliver a session briefing."""
        # Dedup: only one briefing per session start
        ts = event.payload.get("timestamp", "")
        if ts == self._last_briefing_session_ts:
            return
        self._last_briefing_session_ts = ts

        try:
            with self._session_factory() as session:
                briefing = self._generate_briefing(
                    session, event.payload.get("life_track")
                )
                if briefing:
                    # First line as LOW notification, full briefing as AMBIENT
                    first_line = briefing.split(".")[0] + "."
                    await notification_router.send(
                        Notification(
                            title="Session briefing",
                            body=first_line,
                            priority=NotificationPriority.LOW,
                            category="briefing",
                        )
                    )
                    # Full briefing to ambient queue
                    if len(briefing) > len(first_line):
                        await notification_router.send(
                            Notification(
                                title="Full briefing",
                                body=briefing,
                                priority=NotificationPriority.AMBIENT,
                                category="briefing",
                            )
                        )
                    logger.info("Session briefing generated")
        except Exception:
            logger.exception("Session briefing error")

    def _generate_briefing(
        self, session: Any, life_track: str | None
    ) -> str | None:
        """Build a concise briefing from current state."""
        import json
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import func, select

        from deadline_agent.models import Task

        now = datetime.now(UTC)
        parts: list[str] = []

        # Active life context
        try:
            from deadline_agent.store.context_repository import (
                LifeContextRepository,
            )

            ctx_repo = LifeContextRepository(session)
            active_contexts = ctx_repo.get_active()
            if active_contexts:
                ctx = active_contexts[0]
                label = ctx.label or ctx.season.replace("_", " ")
                parts.append(f"You're in {label}")
        except Exception:
            pass

        # Overdue count
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
            parts.append(f"{overdue_count} overdue")

        # Due today count
        end_of_today = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        due_today = (
            session.execute(
                select(func.count(Task.id))
                .where(Task.status == "pending")
                .where(Task.due_date_iso >= now.isoformat())
                .where(Task.due_date_iso < end_of_today.isoformat())
            ).scalar()
            or 0
        )
        if due_today > 0:
            parts.append(f"{due_today} due today")

        # Peak window timing
        try:
            from deadline_agent.store.pattern_repository import PatternRepository

            pattern_repo = PatternRepository(session)
            peak_patterns = pattern_repo.get_by_type("peak_hours")
            for p in peak_patterns:
                data = json.loads(p.value)
                if data.get("rank") == "primary":
                    from zoneinfo import ZoneInfo

                    _est = ZoneInfo("America/New_York")
                    local_now = now.astimezone(_est)
                    start_hour = int(data.get("start_hour", 0))
                    minutes_until = start_hour * 60 - (
                        local_now.hour * 60 + local_now.minute
                    )
                    if 0 < minutes_until <= 120:
                        label = data.get("label", f"{start_hour}:00")
                        parts.append(
                            f"productive window ({label}) starts in {minutes_until} min"
                        )
                    break
        except Exception:
            pass

        # Recruiting context if relevant
        if life_track == "recruiting":
            try:
                from deadline_agent.store.recruiting_repository import (
                    RecruitingRepository,
                )

                repo = RecruitingRepository(session)
                active = repo.list_active()
                if active:
                    parts.append(f"{len(active)} active application(s)")
            except Exception:
                pass

        if not parts:
            return None

        return ". ".join(parts) + "."
