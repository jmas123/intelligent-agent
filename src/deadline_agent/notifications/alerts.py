"""Pre-deadline alert engine (24h and 2h alerts)."""

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from deadline_agent.models import Task
from deadline_agent.notifications.macos import send_notification

logger = logging.getLogger(__name__)


def _parse_due_date(due_str: str) -> datetime | None:
    """Parse ISO 8601 date string, returning timezone-aware datetime."""
    try:
        dt = datetime.fromisoformat(due_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        return None


def _hours_until(due: datetime) -> float:
    """Calculate hours from now until the due date."""
    now = datetime.now(UTC)
    delta = due - now
    return delta.total_seconds() / 3600


def check_and_send_alerts(session: Session) -> int:
    """Check pending tasks and send pre-deadline alerts.

    Sends 24h alert when task is <=24h away, 2h alert when <=2h away.
    Returns the number of alerts sent.
    """
    stmt = (
        select(Task)
        .where(Task.status == "pending")
        .where(Task.due_date_iso.is_not(None))
        .where(or_(Task.alert_24h_sent.is_(False), Task.alert_2h_sent.is_(False)))
    )
    tasks = list(session.scalars(stmt).all())
    alerts_sent = 0

    est = ZoneInfo("America/New_York")

    for task in tasks:
        due = _parse_due_date(task.due_date_iso)  # type: ignore[arg-type]
        if due is None:
            continue

        hours = _hours_until(due)
        due_est = due.astimezone(est).strftime("%b %d, %Y %I:%M %p %Z")

        # Skip past-due tasks
        if hours < 0:
            continue

        # 2h alert (higher urgency)
        if hours <= 2 and not task.alert_2h_sent:
            send_notification(
                title="Deadline in 2 hours!",
                body=f"{task.title}" + (f" ({task.course})" if task.course else ""),
                subtitle=due_est,
            )
            task.alert_2h_sent = True
            task.alert_24h_sent = True  # Don't send 24h alert if 2h was sent first
            alerts_sent += 1
            logger.info("2h alert sent: %s", task.title)

        # 24h alert
        elif hours <= 24 and not task.alert_24h_sent:
            send_notification(
                title="Deadline tomorrow",
                body=f"{task.title}" + (f" ({task.course})" if task.course else ""),
                subtitle=due_est,
            )
            task.alert_24h_sent = True
            alerts_sent += 1
            logger.info("24h alert sent: %s", task.title)

    session.commit()
    return alerts_sent
