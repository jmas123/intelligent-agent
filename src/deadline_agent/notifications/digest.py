"""Morning digest summary of upcoming deadlines."""

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import Task
from deadline_agent.notifications.macos import send_notification

logger = logging.getLogger(__name__)

USER_TZ = ZoneInfo("America/New_York")


def _relative_day(due_str: str) -> str:
    """Convert ISO date to a relative day label (today, tomorrow, Mon, etc.)."""
    try:
        due = datetime.fromisoformat(due_str)
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return due_str

    # Compare in user's local timezone so "today" matches their day
    due_local = due.astimezone(USER_TZ)
    now_local = datetime.now(USER_TZ)
    delta_days = (due_local.date() - now_local.date()).days

    if delta_days == 0:
        return "today"
    if delta_days == 1:
        return "tomorrow"
    if delta_days < 7:
        return due_local.strftime("%A")  # e.g., "Wednesday"
    return due_local.strftime("%b %d")  # e.g., "Mar 30"


def generate_digest(session: Session, days_ahead: int = 7) -> str | None:
    """Build a digest of pending tasks due in the next N days.

    Returns formatted text, or None if no upcoming tasks.
    """
    cutoff = (datetime.now(UTC) + timedelta(days=days_ahead)).isoformat()

    stmt = (
        select(Task)
        .where(Task.status == "pending")
        .where(Task.due_date_iso.is_not(None))
        .where(Task.due_date_iso <= cutoff)
        .order_by(Task.due_date_iso.asc())
    )
    tasks = list(session.scalars(stmt).all())

    # Filter out past-due tasks
    now_iso = datetime.now(UTC).isoformat()
    tasks = [t for t in tasks if (t.due_date_iso or "") >= now_iso]

    if not tasks:
        return None

    lines: list[str] = [f"You have {len(tasks)} upcoming deadline(s):"]
    for task in tasks:
        urgency = "!" * task.urgency_score
        day = _relative_day(task.due_date_iso) if task.due_date_iso else "no date"
        course = f" ({task.course})" if task.course else ""
        lines.append(f"  [{urgency}] {task.title}{course} — {day}")

    return "\n".join(lines)


async def generate_digest_v2(session: Session) -> str | None:
    """LLM-powered digest: state snapshot → natural language briefing.

    Falls back to generate_digest() if LLM is unavailable.
    """
    try:
        from deadline_agent.reasoning.engine import generate_summary

        summary = await generate_summary(session)
        if summary:
            return summary
    except Exception:
        logger.warning("LLM digest generation failed, falling back to v1")

    return generate_digest(session)


def send_digest(session: Session) -> bool:
    """Generate and send morning digest notification.

    Returns True if digest was sent, False if no tasks or sending failed.
    """
    text = generate_digest(session)
    if text is None:
        logger.info("No upcoming tasks for digest")
        return False

    # Notification body is limited — use first few lines
    lines = text.split("\n")
    summary = lines[0]
    body = "\n".join(lines[1:6])  # Up to 5 tasks
    if len(lines) > 6:
        body += f"\n  ...and {len(lines) - 6} more"

    return send_notification(
        title="Deadline Agent — Morning Digest",
        body=body,
        subtitle=summary,
    )


async def send_digest_v2(session: Session) -> bool:
    """Generate LLM digest and send as notification. Falls back to v1."""
    text = await generate_digest_v2(session)
    if text is None:
        logger.info("No upcoming tasks for digest")
        return False

    # Truncate for notification
    lines = text.split("\n")
    body = "\n".join(lines[:6])
    if len(lines) > 6:
        body += "\n..."

    return send_notification(
        title="Deadline Agent — Morning Digest",
        body=body,
    )
