"""Compound trigger engine for anticipatory state-change-driven alerts."""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from deadline_agent.config import settings
from deadline_agent.events import (
    ACTIVITY_DROP_DETECTED,
    FILE_ACTIVITY_RECORDED,
    RECRUITING_STATUS_CHANGED,
    TASK_CREATED,
    Event,
    EventBus,
    event_bus,
)
from deadline_agent.models import FileActivity, Task, WorkSession
from deadline_agent.notifications.macos import send_notification

logger = logging.getLogger(__name__)


class ActivityBaseline:
    """Rolling 7-day file activity baseline. Detects drops."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory
        self._last_check: datetime | None = None
        self._check_interval = timedelta(hours=1)
        self._dropped = False

    async def check_and_emit(self) -> None:
        """Compare today's activity to 7-day average. Emit if dropped."""
        now = datetime.now(UTC)
        if (
            self._last_check
            and (now - self._last_check) < self._check_interval
        ):
            return
        self._last_check = now

        with self._session_factory() as session:
            seven_days_ago = now - timedelta(days=7)
            stmt = (
                select(
                    func.date(FileActivity.created_at).label("day"),
                    func.count(FileActivity.id).label("cnt"),
                )
                .where(FileActivity.created_at >= seven_days_ago)
                .group_by(func.date(FileActivity.created_at))
            )
            rows = session.execute(stmt).all()

            if len(rows) < 2:
                self._dropped = False
                return

            daily_counts = {str(row.day): row.cnt for row in rows}
            today_str = now.strftime("%Y-%m-%d")
            today_count = daily_counts.get(today_str, 0)

            prev_counts = [
                c for d, c in daily_counts.items() if d != today_str
            ]
            if not prev_counts:
                self._dropped = False
                return
            avg = sum(prev_counts) / len(prev_counts)

            threshold = settings.activity_drop_threshold_pct / 100.0
            if avg > 0 and today_count < avg * (1 - threshold):
                self._dropped = True
                drop_pct = round((1 - today_count / avg) * 100, 1)
                await event_bus.emit(
                    Event(
                        type=ACTIVITY_DROP_DETECTED,
                        payload={
                            "today": today_count,
                            "avg": round(avg, 1),
                            "drop_pct": drop_pct,
                        },
                    )
                )
            else:
                self._dropped = False

    def is_dropped(self) -> bool:
        """Return cached drop state for compound trigger use."""
        return self._dropped


class CompoundTriggerEngine:
    """Evaluates multi-signal conditions and fires actions."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory
        self.activity_baseline = ActivityBaseline(session_factory)
        # Prevent duplicate notifications within the same day
        self._notified_today: set[str] = set()
        self._last_clear_date: str = ""

    def register(self, bus: EventBus) -> None:
        """Subscribe to relevant event types."""
        bus.subscribe(TASK_CREATED, self._on_task_created)
        bus.subscribe(
            FILE_ACTIVITY_RECORDED, self._on_file_activity
        )
        bus.subscribe(
            RECRUITING_STATUS_CHANGED, self._on_recruiting_change
        )
        bus.subscribe(
            ACTIVITY_DROP_DETECTED, self._on_activity_drop
        )

    def _clear_daily_dedup(self) -> None:
        """Reset dedup set at midnight."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if today != self._last_clear_date:
            self._notified_today.clear()
            self._last_clear_date = today

    def _should_notify(self, key: str) -> bool:
        """Check and mark a notification key to prevent duplicates."""
        self._clear_daily_dedup()
        if key in self._notified_today:
            return False
        self._notified_today.add(key)
        return True

    async def _on_task_created(self, event: Event) -> None:
        """New task extracted — check for deadline cluster."""
        if not settings.enable_anticipatory_triggers:
            return
        await self._check_deadline_cluster()

    async def _on_file_activity(self, event: Event) -> None:
        """File activity recorded — update activity baseline."""
        if not settings.enable_anticipatory_triggers:
            return
        await self.activity_baseline.check_and_emit()

    async def _on_activity_drop(self, event: Event) -> None:
        """Activity dropped below threshold — check compound conditions."""
        if not settings.enable_anticipatory_triggers:
            return
        await self._check_deadline_cluster()

    async def _on_recruiting_change(self, event: Event) -> None:
        """Recruiting status changed — immediate notification."""
        if not settings.enable_anticipatory_triggers:
            return
        if not settings.recruiting_fast_path_notify:
            return
        if not settings.enable_notifications:
            return

        company = event.payload.get("company", "Unknown")
        new_status = event.payload.get("new_status", "")
        subject = event.payload.get("subject", "")

        key = f"recruiting:{company}:{new_status}"
        if not self._should_notify(key):
            return

        send_notification(
            title=f"Recruiting: {company}",
            body=f"Status → {new_status}",
            subtitle=subject[:80] if subject else "",
        )
        logger.info(
            "Recruiting fast-path notification: %s → %s",
            company,
            new_status,
        )

    async def _check_deadline_cluster(self) -> None:
        """3+ deadlines in N days + activity drop → nudge."""
        if not settings.enable_notifications:
            return

        key = "deadline_cluster"
        if not self._should_notify(key):
            return

        with self._session_factory() as session:
            now = datetime.now(UTC)
            cutoff = (
                now + timedelta(days=settings.deadline_cluster_days)
            ).isoformat()
            stmt = (
                select(func.count(Task.id))
                .where(Task.status == "pending")
                .where(Task.due_date_iso.is_not(None))
                .where(Task.due_date_iso >= now.isoformat())
                .where(Task.due_date_iso <= cutoff)
            )
            count = session.execute(stmt).scalar() or 0

            if count >= settings.deadline_cluster_count:
                if self.activity_baseline.is_dropped():
                    send_notification(
                        title="Workload spike detected",
                        body=(
                            f"{count} deadlines in the next "
                            f"{settings.deadline_cluster_days} days "
                            f"and your activity has dropped."
                        ),
                    )
                    logger.info(
                        "Compound trigger: %d deadlines + activity drop",
                        count,
                    )
                elif count >= settings.deadline_cluster_count + 2:
                    # Large cluster even without activity drop
                    send_notification(
                        title="Heavy deadline week ahead",
                        body=f"{count} deadlines in the next "
                        f"{settings.deadline_cluster_days} days.",
                    )

            # Historical spike prediction
            await self._check_historical_spike(session, count)

    async def _check_historical_spike(
        self, session: Any, current_count: int
    ) -> None:
        """Compare current week to historical patterns and warn if spike detected."""
        key = "historical_spike"
        if not self._should_notify(key):
            return

        try:
            from deadline_agent.reasoning.context_window import (
                _get_current_semester_week,
                predict_workload_spikes,
            )
            from deadline_agent.store.context_repository import (
                LifeContextRepository,
            )

            semester_week = _get_current_semester_week(session)
            if semester_week is None:
                return

            ctx_repo = LifeContextRepository(session)
            contexts = ctx_repo.get_active()

            predictions = predict_workload_spikes(
                session, semester_week, [], contexts
            )
            if predictions:
                send_notification(
                    title="Historical pattern warning",
                    body=predictions[0][:300],
                )
                logger.info("Historical spike prediction triggered")
        except Exception:
            logger.debug(
                "Historical spike check skipped (data unavailable)"
            )


async def peak_window_nudge_scheduler(
    session_factory: Any,
) -> None:
    """Check if peak productive window is approaching + urgent unstarted tasks."""
    import asyncio
    from zoneinfo import ZoneInfo

    from deadline_agent.store.pattern_repository import PatternRepository

    logger.info("Peak window nudge scheduler started")
    _est = ZoneInfo("America/New_York")
    nudged_today: set[tuple[str, int]] = set()
    last_clear_date = ""

    while True:
        await asyncio.sleep(
            settings.peak_nudge_check_interval_minutes * 60
        )
        if not settings.enable_notifications:
            continue
        if not settings.enable_anticipatory_triggers:
            continue

        try:
            # Clear dedup set at midnight
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            if today != last_clear_date:
                nudged_today.clear()
                last_clear_date = today

            with session_factory() as session:
                repo = PatternRepository(session)
                peak_patterns = repo.get_by_type("peak_hours")
                if not peak_patterns:
                    continue

                # Find primary peak window
                primary = None
                for p in peak_patterns:
                    data = json.loads(p.value)
                    if data.get("rank") == "primary":
                        primary = data
                        break
                if not primary:
                    continue

                now_local = datetime.now(_est)
                start_hour = int(primary.get("start_hour", 0))
                minutes_until = (
                    start_hour * 60
                    - (now_local.hour * 60 + now_local.minute)
                )
                if minutes_until < 0:
                    minutes_until += 1440

                if not (
                    0
                    < minutes_until
                    <= settings.peak_nudge_minutes_before
                ):
                    continue

                # Find unstarted urgent tasks due within 24h
                now_utc = datetime.now(UTC)
                cutoff_24h = (now_utc + timedelta(hours=24)).isoformat()
                stmt = (
                    select(Task)
                    .where(Task.status == "pending")
                    .where(Task.due_date_iso.is_not(None))
                    .where(Task.due_date_iso >= now_utc.isoformat())
                    .where(Task.due_date_iso <= cutoff_24h)
                    .where(
                        ~Task.id.in_(
                            select(WorkSession.task_id).distinct()
                        )
                    )
                    .order_by(Task.urgency_score.desc())
                    .limit(3)
                )
                tasks = list(session.scalars(stmt).all())
                if not tasks:
                    continue

                # Pick the most urgent unstarted task
                task = tasks[0]
                nudge_key = (today, task.id)
                if nudge_key in nudged_today:
                    continue
                nudged_today.add(nudge_key)

                course = f" ({task.course})" if task.course else ""
                send_notification(
                    title="Peak window starting soon",
                    body=(
                        f"{task.title}{course} is due in <24h "
                        f"and unstarted. Your productive window "
                        f"starts in {minutes_until} min."
                    ),
                )
                logger.info(
                    "Peak window nudge: %s (due <24h, window in %dmin)",
                    task.title,
                    minutes_until,
                )

        except Exception:
            logger.exception("Peak window nudge error")


async def follow_up_staleness_scheduler(
    session_factory: Any,
) -> None:
    """Check for recruiting applications that need follow-up.

    Runs daily and notifies about applications with no signal in 7+ days.
    """
    import asyncio

    from deadline_agent.store.recruiting_repository import (
        RecruitingRepository,
    )

    logger.info("Follow-up staleness scheduler started")
    notified_today: set[str] = set()
    last_clear_date = ""

    while True:
        # Check every 6 hours
        await asyncio.sleep(6 * 3600)
        if not settings.enable_notifications:
            continue
        if not settings.enable_anticipatory_triggers:
            continue

        try:
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            if today != last_clear_date:
                notified_today.clear()
                last_clear_date = today

            with session_factory() as session:
                repo = RecruitingRepository(session)
                active_apps = repo.list_active()
                now = datetime.now(UTC)

                for app in active_apps:
                    signal_dt = app.last_signal_at
                    if signal_dt is None:
                        continue
                    if signal_dt.tzinfo is None:
                        signal_dt = signal_dt.replace(tzinfo=UTC)
                    days = (now - signal_dt).days

                    # Notify at 7 and 14 day marks
                    if days < 7:
                        continue
                    if app.status in ("offer", "closed"):
                        continue

                    key = f"{app.company_normalized}:{days // 7}"
                    if key in notified_today:
                        continue
                    notified_today.add(key)

                    send_notification(
                        title="Follow-up needed",
                        body=(
                            f"{app.company_name}: no response "
                            f"in {days} days (status: {app.status})"
                        ),
                        subtitle="Consider sending a follow-up",
                    )
                    logger.info(
                        "Follow-up staleness alert: %s (%d days)",
                        app.company_name,
                        days,
                    )

        except Exception:
            logger.exception("Follow-up staleness check error")
