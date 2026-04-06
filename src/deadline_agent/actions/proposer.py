"""Create action proposals for calendar blocks and Gmail drafts."""

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from deadline_agent.models import ProposedAction, Task
from deadline_agent.store.action_repository import ActionRepository

EST = ZoneInfo("America/New_York")


def _format_iso_as_est(iso_str: str) -> str:
    """Convert an ISO datetime string to a readable EST label."""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(EST).strftime("%b %d, %I:%M %p %Z").lstrip("0")
    except (ValueError, TypeError):
        return iso_str[:16]


def propose_time_block(
    session: Session,
    task: Task,
    start_iso: str,
    end_iso: str,
    description: str | None = None,
) -> ProposedAction:
    """Propose a calendar time block for working on a task."""
    summary = f"Work on: {task.title}"
    if task.course:
        summary += f" ({task.course})"

    payload = {
        "summary": summary,
        "start_iso": start_iso,
        "end_iso": end_iso,
    }
    if description:
        payload["description"] = description

    repo = ActionRepository(session)
    return repo.propose(
        {
            "type": "calendar_block",
            "task_id": task.id,
            "title": f"Block time: {_format_iso_as_est(start_iso)} — {task.title}",
            "payload": json.dumps(payload),
        }
    )


def propose_follow_up_draft(
    session: Session,
    task: Task,
    to: str,
    subject: str,
    body: str,
) -> ProposedAction:
    """Propose a Gmail follow-up draft related to a task."""
    payload = {
        "to": to,
        "subject": subject,
        "body": body,
    }

    repo = ActionRepository(session)
    return repo.propose(
        {
            "type": "gmail_draft",
            "task_id": task.id,
            "title": f"Draft to {to}: {subject[:50]}",
            "payload": json.dumps(payload),
        }
    )
