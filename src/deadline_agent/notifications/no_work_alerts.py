"""No-work-detected alert engine."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.config import settings
from deadline_agent.models import Task
from deadline_agent.notifications.alerts import _hours_until, _parse_due_date
from deadline_agent.store.file_repository import FileActivityRepository

logger = logging.getLogger(__name__)


async def check_no_work_alerts(session: Session) -> int:
    """Send alerts for tasks approaching deadlines with no linked file activity.

    Routes through intervention engine. Returns the number of alerts sent.
    """
    from deadline_agent.reasoning.intervention_engine import get_intervention_engine

    engine = get_intervention_engine()

    stmt = (
        select(Task)
        .where(Task.status == "pending")
        .where(Task.due_date_iso.is_not(None))
        .where(Task.no_work_alert_sent.is_(False))
    )
    tasks = list(session.scalars(stmt).all())

    file_repo = FileActivityRepository(session)
    alerts_sent = 0

    for task in tasks:
        due = _parse_due_date(task.due_date_iso)  # type: ignore[arg-type]
        if due is None:
            continue

        hours = _hours_until(due)

        # Skip past-due or tasks not yet in the alert window
        if hours < 0 or hours > settings.no_work_alert_hours:
            continue

        # Check for linked file activity
        if file_repo.has_activity_for_task(task.id, since_hours=settings.no_work_alert_hours):
            continue

        # No work detected — route through intervention engine
        course_str = f" ({task.course})" if task.course else ""
        await engine.evaluate(
            session,
            task_id=task.id,
            risk_level="medium",
            category="no_work",
            title="No work detected",
            body=f"{task.title}{course_str} is due in {int(hours)}h — no matching files found",
        )
        task.no_work_alert_sent = True
        alerts_sent += 1
        logger.info("No-work alert sent: %s", task.title)

    session.commit()
    return alerts_sent
