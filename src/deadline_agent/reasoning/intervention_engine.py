"""Intervention Engine — decides when, how, and how hard to interrupt the user.

Policy-based decision layer between risk signals and notification delivery.
All notification paths in the system must route through this engine.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from deadline_agent.config import settings
from deadline_agent.events import (
    INTERVENTION_FIRED,
    INTERVENTION_SUPPRESSED,
    TASK_STATUS_CHANGED,
    Event,
    EventBus,
)
from deadline_agent.models import InterventionRecord, Task
from deadline_agent.notifications.channels import (
    Notification,
    NotificationPriority,
    notification_router,
)
from deadline_agent.store.intervention_repository import InterventionRepository

logger = logging.getLogger(__name__)

# Risk level → intervention tier
RISK_TIER_MAP: dict[str, str] = {
    "low": "silent",
    "medium": "menubar",
    "high": "notification",
    "critical": "voice",
}

# Tier ordering for escalation
TIER_ORDER = ["silent", "menubar", "notification", "voice"]


def _deadline_is_stale(due_date_iso: str | None) -> bool:
    """Whether a deadline is far enough in the past that nagging is pointless."""
    if not due_date_iso:
        return False
    try:
        due = datetime.fromisoformat(due_date_iso)
    except ValueError:
        return False
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    age_days = (datetime.now(UTC) - due).days
    return age_days > settings.intervention_stale_deadline_days


class InterventionEngine:
    """Evaluates intervention policy and decides whether to fire, suppress, or escalate."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    def register(self, bus: EventBus) -> None:
        """Subscribe to events for de-escalation on user action."""
        bus.subscribe(TASK_STATUS_CHANGED, self._on_task_status_changed)

    async def evaluate(
        self,
        session: Session,
        *,
        task_id: int | None,
        risk_level: str,
        category: str,
        title: str,
        body: str,
        force: bool = False,
    ) -> InterventionRecord:
        """Core decision method. Every notification path calls this.

        Returns the InterventionRecord (fired=True or fired=False with reason).
        """
        repo = InterventionRepository(session)
        tier = RISK_TIER_MAP.get(risk_level, "notification")

        # --- Gate 1: Silent tier → log only ---
        if tier == "silent" and not force:
            record = repo.create_record(
                task_id=task_id,
                risk_level=risk_level,
                intervention_tier=tier,
                category=category,
                title=title,
                body=body,
                fired=False,
                suppression_reason="silent_tier",
            )
            logger.debug("Intervention silent-logged: %s", title)
            return record

        # --- Gate 2: Quiet hours ---
        if not force and self._in_quiet_hours():
            record = repo.create_record(
                task_id=task_id,
                risk_level=risk_level,
                intervention_tier=tier,
                category=category,
                title=title,
                body=body,
                fired=False,
                suppression_reason="quiet_hours",
            )
            if risk_level in ("high", "critical") and task_id is not None:
                self._schedule_escalation(repo, record, task_id, tier)
            logger.debug("Intervention suppressed (quiet hours): %s", title)
            return record

        # --- Gate 3: Cooldown ---
        if not force:
            last_fired = repo.get_last_fired()
            if last_fired and last_fired.created_at.tzinfo is None:
                last_ts = last_fired.created_at.replace(tzinfo=UTC)
            elif last_fired:
                last_ts = last_fired.created_at
            else:
                last_ts = None

            if last_ts is not None:
                elapsed_min = (datetime.now(UTC) - last_ts).total_seconds() / 60
                if elapsed_min < settings.intervention_cooldown_minutes:
                    if risk_level in ("low", "medium"):
                        record = repo.create_record(
                            task_id=task_id,
                            risk_level=risk_level,
                            intervention_tier=tier,
                            category=category,
                            title=title,
                            body=body,
                            fired=False,
                            suppression_reason="cooldown",
                        )
                        if task_id is not None:
                            self._schedule_escalation(repo, record, task_id, tier)
                        logger.debug("Intervention suppressed (cooldown): %s", title)
                        return record

        # --- Gate 4: Budget ---
        if not force:
            fired_today = repo.count_fired_today()
            if fired_today >= settings.intervention_max_interrupts_per_day:
                if risk_level != "critical":
                    record = repo.create_record(
                        task_id=task_id,
                        risk_level=risk_level,
                        intervention_tier=tier,
                        category=category,
                        title=title,
                        body=body,
                        fired=False,
                        suppression_reason="budget_exhausted",
                    )
                    if task_id is not None:
                        self._schedule_escalation(repo, record, task_id, tier)
                    logger.debug("Intervention suppressed (budget): %s", title)
                    return record

        # --- Gate 5: Focus session ---
        if not force and risk_level != "critical":
            if self._in_focus_session():
                record = repo.create_record(
                    task_id=task_id,
                    risk_level=risk_level,
                    intervention_tier=tier,
                    category=category,
                    title=title,
                    body=body,
                    fired=False,
                    suppression_reason="focus_session",
                )
                if task_id is not None:
                    self._schedule_escalation(repo, record, task_id, tier)
                logger.debug("Intervention suppressed (focus session): %s", title)
                return record

        # --- All gates passed: FIRE ---
        record = repo.create_record(
            task_id=task_id,
            risk_level=risk_level,
            intervention_tier=tier,
            category=category,
            title=title,
            body=body,
            fired=True,
        )

        await self._deliver(tier, title, body, category)

        # Schedule escalation for medium/high in case user ignores
        if risk_level in ("medium", "high") and task_id is not None:
            self._schedule_escalation(repo, record, task_id, tier)

        # Emit event
        from deadline_agent.events import event_bus

        await event_bus.emit(
            Event(
                type=INTERVENTION_FIRED,
                payload={
                    "record_id": record.id,
                    "task_id": task_id,
                    "tier": tier,
                    "risk_level": risk_level,
                    "category": category,
                },
            )
        )

        logger.info("Intervention fired [%s/%s]: %s", tier, risk_level, title)
        return record

    async def process_escalations(self, session: Session) -> int:
        """Check and fire any pending escalations whose wait time has elapsed.

        Called periodically from alert_scheduler. Returns count fired.
        """
        repo = InterventionRepository(session)
        pending = repo.get_pending_escalations()
        fired_count = 0

        for esc in pending:
            # Drop escalations whose task is finished or long past its deadline —
            # otherwise a completed task keeps re-escalating night after night.
            task = session.get(Task, esc.task_id)
            if task is None or task.status != "pending":
                repo.cancel_escalations_for_task(esc.task_id, "task_closed")
                continue
            if _deadline_is_stale(task.due_date_iso):
                repo.cancel_escalations_for_task(esc.task_id, "deadline_stale")
                continue

            # Stop after a bounded number of hops so a chain can't run forever
            if (
                repo.escalation_chain_depth(esc.original_record_id)
                >= settings.intervention_max_escalations
            ):
                repo.cancel_escalations_for_task(esc.task_id, "max_escalations")
                continue

            # Re-check current risk — may have decreased
            from deadline_agent.store.risk_repository import RiskRepository

            risk_repo = RiskRepository(session)
            current_risk = risk_repo.get_for_task(esc.task_id)

            if current_risk and current_risk.risk_level == "low":
                repo.cancel_escalations_for_task(esc.task_id, "risk_decreased")
                continue

            original = session.get(InterventionRecord, esc.original_record_id)
            if not original:
                repo.mark_escalation_fired(esc.id)
                continue

            current_risk_level = current_risk.risk_level if current_risk else "high"

            title = (
                original.title
                if original.title.startswith("[Escalated]")
                else f"[Escalated] {original.title}"
            )
            escalated_record = await self.evaluate(
                session,
                task_id=esc.task_id,
                risk_level=current_risk_level,
                category=original.category,
                title=title,
                body=original.body,
            )
            escalated_record.escalated_from_id = esc.original_record_id
            session.flush()

            repo.mark_escalation_fired(esc.id)
            fired_count += 1

        return fired_count

    async def _deliver(self, tier: str, title: str, body: str, category: str) -> None:
        """Deliver the intervention through the appropriate channel."""
        if tier == "voice" and settings.intervention_enable_voice:
            try:
                import subprocess

                text = f"{title}. {body}"
                subprocess.Popen(
                    ["say", "-v", "Samantha", text[:200]],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                logger.warning("Voice delivery failed, falling back to notification")
            await notification_router.send(
                Notification(
                    title=title,
                    body=body,
                    category=category,
                    priority=NotificationPriority.HIGH,
                )
            )
        elif tier in ("notification", "voice"):
            await notification_router.send(
                Notification(
                    title=title,
                    body=body,
                    category=category,
                    priority=NotificationPriority.HIGH,
                )
            )
        elif tier == "menubar":
            await notification_router.send(
                Notification(
                    title=title,
                    body=body,
                    category=category,
                    priority=NotificationPriority.AMBIENT,
                )
            )
        # silent: no delivery

    def _in_quiet_hours(self) -> bool:
        """Check if current local time is within quiet hours."""
        from zoneinfo import ZoneInfo

        now_local = datetime.now(ZoneInfo("America/New_York"))
        start_h, start_m = (int(x) for x in settings.intervention_quiet_hours_start.split(":"))
        end_h, end_m = (int(x) for x in settings.intervention_quiet_hours_end.split(":"))

        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m
        now_minutes = now_local.hour * 60 + now_local.minute

        if start_minutes > end_minutes:
            # Spans midnight: e.g., 22:00 - 07:00
            return now_minutes >= start_minutes or now_minutes < end_minutes
        return start_minutes <= now_minutes < end_minutes

    def _in_focus_session(self) -> bool:
        """Check if user is in an active focus/work session."""
        try:
            from deadline_agent.awareness.ambient_state import ambient_state

            return ambient_state.session_active and not ambient_state.is_idle()
        except Exception:
            return False

    def _schedule_escalation(
        self,
        repo: InterventionRepository,
        record: InterventionRecord,
        task_id: int,
        current_tier: str,
    ) -> None:
        """Schedule an escalation to the next tier if applicable."""
        if current_tier not in TIER_ORDER:
            return
        current_idx = TIER_ORDER.index(current_tier)
        if current_idx >= len(TIER_ORDER) - 1:
            return  # already at highest tier

        next_tier = TIER_ORDER[current_idx + 1]
        escalate_at = datetime.now(UTC) + timedelta(
            hours=settings.intervention_escalation_wait_hours
        )
        repo.create_escalation(
            task_id=task_id,
            original_record_id=record.id,
            current_tier=current_tier,
            next_tier=next_tier,
            escalate_after=escalate_at,
        )

    async def _on_task_status_changed(self, event: Event) -> None:
        """De-escalation: cancel pending escalations and mark effectiveness."""
        task_id = event.payload.get("task_id")
        new_status = event.payload.get("new_status")
        if not task_id:
            return

        if new_status in ("done", "dismissed"):
            with self._session_factory() as session:
                repo = InterventionRepository(session)
                cancelled = repo.cancel_escalations_for_task(task_id, "user_acted")
                if cancelled:
                    logger.info(
                        "De-escalated %d pending escalation(s) for task %d",
                        cancelled,
                        task_id,
                    )
                acted = repo.mark_user_acted(task_id)
                if acted:
                    logger.info(
                        "Marked %d intervention(s) effective for task %d",
                        acted,
                        task_id,
                    )
                session.commit()


# --- Module-level singleton ---

_engine: InterventionEngine | None = None


def init_intervention_engine(session_factory: Any) -> InterventionEngine:
    """Initialize the module-level singleton. Called once from main.py."""
    global _engine
    _engine = InterventionEngine(session_factory)
    return _engine


def get_intervention_engine() -> InterventionEngine:
    """Get the module-level intervention engine singleton."""
    if _engine is None:
        raise RuntimeError(
            "InterventionEngine not initialized — call init_intervention_engine() first"
        )
    return _engine
