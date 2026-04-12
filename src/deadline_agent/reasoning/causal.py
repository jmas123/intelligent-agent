"""Causal reasoning: compute why tasks are at risk and effort-adjusted estimates."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import BehavioralPattern, Task, WorkSession
from deadline_agent.reasoning.scheduler import EFFORT_MAP

logger = logging.getLogger(__name__)


def _get_pattern_value(
    patterns: list[BehavioralPattern],
    pattern_type: str,
    pattern_key: str,
) -> dict | None:
    """Look up a pattern's parsed JSON value."""
    for p in patterns:
        if p.pattern_type == pattern_type and p.pattern_key == pattern_key:
            try:
                return json.loads(p.value)
            except (json.JSONDecodeError, TypeError):
                return None
    return None


def _has_work_started(task_id: int, session: Session) -> bool:
    """Check if any WorkSession exists for a task."""
    stmt = select(WorkSession.id).where(WorkSession.task_id == task_id).limit(1)
    return session.scalars(stmt).first() is not None


def compute_causal_context(
    tasks: list[Task],
    patterns: list[BehavioralPattern],
    now: datetime,
    session: Session,
) -> list[str]:
    """Generate causal reasoning statements for at-risk and overdue tasks.

    For each task, uses lead_time and procrastination patterns to explain
    when work should have started, and compares to actual state.
    """
    statements: list[str] = []

    for task in tasks:
        if not task.due_date_iso:
            continue

        try:
            due = datetime.fromisoformat(task.due_date_iso)
            if due.tzinfo is None:
                due = due.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            continue

        task_type = task.type or "assignment"
        course = f" ({task.course})" if task.course else ""
        started = _has_work_started(task.id, session)

        # Look up lead time pattern
        lead_time = _get_pattern_value(patterns, "lead_time", task_type)
        if lead_time:
            mean_hours = lead_time.get("mean_hours", 0)
            if mean_hours > 0:
                needed_start = due - timedelta(hours=mean_hours)
                if now > needed_start and not started:
                    days_late = (now - needed_start).total_seconds() / 86400
                    lead_days = mean_hours / 24

                    statements.append(
                        f"{task.title}{course}: Based on your lead time of "
                        f"{lead_days:.1f} days for {task_type}s, you needed to "
                        f"start {needed_start.strftime('%A')}. It's now "
                        f"{now.strftime('%A')} ({days_late:.0f}d late) "
                        f"with no work detected."
                    )
                elif now > needed_start and started:
                    statements.append(
                        f"{task.title}{course}: You started later than your "
                        f"typical {mean_hours / 24:.1f}-day lead time, but "
                        f"work is underway."
                    )
                continue

        # Fallback: use procrastination pattern
        proc = _get_pattern_value(patterns, "procrastination", task_type)
        if proc:
            mean_days = proc.get("mean_days_before_deadline", 0)
            if mean_days > 0 and not started:
                days_until_due = (due - now).total_seconds() / 86400
                if days_until_due < mean_days:
                    statements.append(
                        f"{task.title}{course}: You typically start {task_type}s "
                        f"{mean_days:.1f} days before deadline, but this is due "
                        f"in {days_until_due:.1f} days with no work detected."
                    )

    return statements


def compute_effort_context(
    tasks: list[Task],
    patterns: list[BehavioralPattern],
    session: Session,
) -> list[str]:
    """Generate effort-adjusted estimates using effort_accuracy patterns."""
    statements: list[str] = []

    for task in tasks:
        task_type = task.type or "assignment"
        course = f" ({task.course})" if task.course else ""

        base_minutes = EFFORT_MAP.get(task_type, 120)
        if base_minutes == 0:
            continue

        # Adjust by urgency
        urgency_mult = 1.0 + (task.urgency_score - 3) * 0.1
        estimated = base_minutes * urgency_mult

        # Look up effort accuracy pattern
        accuracy = _get_pattern_value(patterns, "effort_accuracy", task_type)
        if accuracy:
            ratio = accuracy.get("ratio", 1.0)
            if abs(ratio - 1.0) > 0.15:  # Only mention if significantly off
                adjusted = estimated * ratio
                est_hours = round(estimated / 60, 1)
                adj_hours = round(adjusted / 60, 1)
                direction = "longer" if ratio > 1.0 else "less time"
                statements.append(
                    f"{task.title}{course}: Estimated {est_hours}h, but based on "
                    f"your {ratio:.1f}x accuracy ratio for {task_type}s, plan for "
                    f"~{adj_hours}h ({direction} than estimated)."
                )

    return statements
