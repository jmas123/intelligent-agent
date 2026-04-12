"""Goal gap computation: compare stated intentions to actual behavior."""

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from deadline_agent.models import FileActivity, Goal

logger = logging.getLogger(__name__)

# Map goal categories to life_track values for activity matching
CATEGORY_TO_TRACK = {
    "academic": "school",
    "recruiting": "recruiting",
    "personal": "project",
}


def compute_goal_gaps(session: Session) -> list[str]:
    """Compare active goals against actual behavior and return gap descriptions.

    Each gap is a human-readable string for the LLM reasoning context.
    """
    goals = list(
        session.scalars(select(Goal).where(Goal.status == "active")).all()
    )

    if not goals:
        return []

    now = datetime.now(UTC)
    week_ago = (now - timedelta(days=7)).isoformat()
    gaps: list[str] = []

    for goal in goals:
        gap = _evaluate_goal(session, goal, week_ago)
        if gap:
            gaps.append(gap)

    return gaps


def _evaluate_goal(session: Session, goal: Goal, since_iso: str) -> str | None:
    """Evaluate a single goal against recent activity.

    Returns a gap description string, or None if on track.
    """
    # Try to match goal category to a life_track
    track = CATEGORY_TO_TRACK.get(goal.category)

    if track:
        # Count file activity events for this track in the past week
        activity_count = session.scalar(
            select(func.count(FileActivity.id))
            .where(FileActivity.created_at >= since_iso)
            .where(FileActivity.life_track == track)
        ) or 0

        if activity_count == 0:
            return (
                f"Goal: \"{goal.description}\" ({goal.category}) — "
                f"zero activity this week"
            )

        # If there's a target metric, try to parse hours
        if goal.target_metric:
            target_info = _parse_target_metric(goal.target_metric)
            if target_info and target_info.get("hours_per_week"):
                # Rough estimate: each file activity ≈ 5 min average
                estimated_hours = (activity_count * 5) / 60
                target_hours = target_info["hours_per_week"]
                if estimated_hours < target_hours * 0.5:
                    pct = int((estimated_hours / target_hours) * 100)
                    return (
                        f"Goal: \"{goal.description}\" — "
                        f"~{estimated_hours:.1f}h this week vs {target_hours}h target "
                        f"({pct}% of goal)"
                    )
    else:
        # For health/social goals, we can only note that they exist
        # and the system has no direct activity signal
        return (
            f"Goal: \"{goal.description}\" ({goal.category}) — "
            f"no activity data available for this category"
        )

    return None


def _parse_target_metric(metric: str) -> dict | None:
    """Try to extract structured info from a target metric string.

    Handles patterns like "4h/week", "10 hours per week", etc.
    """
    import re

    metric_lower = metric.lower().strip()

    # Match "Xh/week" or "X hours/week" or "X hours per week"
    match = re.search(r"(\d+(?:\.\d+)?)\s*h(?:ours?)?\s*(?:/|per)\s*week", metric_lower)
    if match:
        return {"hours_per_week": float(match.group(1))}

    return None
