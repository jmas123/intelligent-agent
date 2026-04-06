"""Smart scheduling: match tasks to calendar gaps and propose time blocks."""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.actions.proposer import propose_time_block
from deadline_agent.models import ProposedAction, Task
from deadline_agent.reasoning.state import UnifiedContext

logger = logging.getLogger(__name__)

# Effort estimates in minutes by task type
EFFORT_MAP: dict[str, int] = {
    "exam": 180,
    "assignment": 120,
    "meeting": 60,
    "reminder": 30,
    "announcement": 0,
}


def estimate_effort(task: Task, session: Session | None = None) -> int:
    """Estimate minutes needed for a task based on type and urgency.

    When a database session is provided, adjusts the estimate using learned
    effort_accuracy patterns from past work sessions.
    """
    base = EFFORT_MAP.get(task.type, 90)
    # Higher urgency = more time (slight adjustment)
    multiplier = 1.0 + (task.urgency_score - 3) * 0.1
    estimated = max(30, int(base * multiplier))

    if session is not None:
        try:
            import json

            from deadline_agent.store.pattern_repository import PatternRepository

            repo = PatternRepository(session)
            pattern = repo.get_one("effort_accuracy", task.type)
            if pattern and pattern.confidence >= 0.5:
                data = json.loads(pattern.value)
                ratio = data.get("ratio", 1.0)
                estimated = max(30, int(estimated * ratio))
        except Exception:
            pass  # Fall back to static estimate

    return estimated


def _has_pending_proposal(session: Session, task_id: int) -> bool:
    """Check if a task already has a pending proposed action."""
    stmt = (
        select(ProposedAction.id)
        .where(ProposedAction.task_id == task_id)
        .where(ProposedAction.status == "proposed")
        .limit(1)
    )
    return session.execute(stmt).first() is not None


def propose_smart_schedule(context: UnifiedContext, session: Session) -> list[ProposedAction]:
    """Match unworked tasks to calendar gaps by priority.

    Returns list of ProposedActions created. Only proposes for tasks that:
    - Are in unworked_deadlines (no file activity)
    - Don't already have a pending proposal
    - Have a calendar gap large enough for the estimated effort
    """
    # Sort tasks by urgency (highest first), then by due date (earliest first)
    tasks = sorted(
        context.unworked_deadlines,
        key=lambda t: (-t.urgency_score, t.due_date_iso or ""),
    )

    # Available gaps (mutable copy, sorted by start)
    available: list[dict[str, Any]] = sorted(
        [dict(g) for g in context.calendar_gaps],
        key=lambda g: str(g.get("start", "")),
    )

    proposals: list[ProposedAction] = []

    for task in tasks:
        if _has_pending_proposal(session, task.id):
            continue

        effort = estimate_effort(task, session)

        # Find first gap that fits
        for i, gap in enumerate(available):
            gap_minutes = int(gap.get("duration_minutes", 0))
            if gap_minutes >= effort:
                # Propose this block
                gap_start = str(gap["start"])
                # Calculate end time based on effort, not full gap
                from datetime import datetime, timedelta

                start_dt = datetime.fromisoformat(gap_start)
                end_dt = start_dt + timedelta(minutes=effort)

                action = propose_time_block(session, task, gap_start, end_dt.isoformat())
                proposals.append(action)

                # Shrink or remove the gap
                remaining = gap_minutes - effort
                if remaining >= 60:
                    available[i] = {
                        "start": end_dt.isoformat(),
                        "end": gap["end"],
                        "duration_minutes": remaining,
                    }
                else:
                    available.pop(i)

                break

    logger.info("Smart scheduler proposed %d time blocks", len(proposals))
    return proposals
