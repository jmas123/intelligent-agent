"""Weekly review: stats aggregation + LLM pattern summary."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from deadline_agent.models import Task
from deadline_agent.reasoning.engine import _call_anthropic, _call_ollama

logger = logging.getLogger(__name__)

WEEKLY_REVIEW_PROMPT = (
    "You are an academic productivity coach. Given the student's weekly stats, "
    "write a brief weekly review (3-5 sentences). Highlight:\n"
    "- What went well (tasks completed on time)\n"
    "- What slipped (overdue tasks)\n"
    "- Patterns you notice (e.g., 'you tend to start late on CS 101')\n"
    "- One actionable suggestion for next week\n\n"
    "Be encouraging but honest."
)


def compute_weekly_stats(session: Session) -> dict[str, Any]:
    """Compute task stats for the past 7 days, grouped by course."""
    now = datetime.now(UTC)
    week_ago = (now - timedelta(days=7)).isoformat()

    # Tasks completed this week
    done = list(
        session.scalars(
            select(Task).where(Task.status == "done").where(Task.updated_at >= week_ago)
        ).all()
    )

    # Tasks that slipped (pending + past due)
    slipped = list(
        session.scalars(
            select(Task)
            .where(Task.status == "pending")
            .where(Task.due_date_iso < now.isoformat())
            .where(Task.due_date_iso.is_not(None))
        ).all()
    )

    # Upcoming next week
    next_week = (now + timedelta(days=7)).isoformat()
    upcoming = list(
        session.scalars(
            select(Task)
            .where(Task.status == "pending")
            .where(Task.due_date_iso >= now.isoformat())
            .where(Task.due_date_iso < next_week)
        ).all()
    )

    # Group by course
    by_course: dict[str, dict[str, int]] = {}
    for t in done:
        c = t.course or "Other"
        by_course.setdefault(c, {"done": 0, "slipped": 0, "upcoming": 0})
        by_course[c]["done"] += 1
    for t in slipped:
        c = t.course or "Other"
        by_course.setdefault(c, {"done": 0, "slipped": 0, "upcoming": 0})
        by_course[c]["slipped"] += 1
    for t in upcoming:
        c = t.course or "Other"
        by_course.setdefault(c, {"done": 0, "slipped": 0, "upcoming": 0})
        by_course[c]["upcoming"] += 1

    return {
        "done_count": len(done),
        "slipped_count": len(slipped),
        "upcoming_count": len(upcoming),
        "by_course": by_course,
        "done_tasks": [t.title for t in done],
        "slipped_tasks": [t.title for t in slipped],
    }


def _stats_to_prompt(stats: dict[str, Any]) -> str:
    """Format weekly stats as text for LLM input."""
    lines = [
        f"Completed: {stats['done_count']} tasks",
        f"Slipped (overdue): {stats['slipped_count']} tasks",
        f"Upcoming next week: {stats['upcoming_count']} tasks",
        "",
    ]

    if stats["done_tasks"]:
        lines.append("Completed tasks:")
        for t in stats["done_tasks"]:
            lines.append(f"  - {t}")
        lines.append("")

    if stats["slipped_tasks"]:
        lines.append("Slipped tasks:")
        for t in stats["slipped_tasks"]:
            lines.append(f"  - {t}")
        lines.append("")

    by_course = stats.get("by_course", {})
    if by_course:
        lines.append("By course:")
        for course, data in by_course.items():
            d = data.get("done", 0)
            s = data.get("slipped", 0)
            u = data.get("upcoming", 0)
            lines.append(f"  {course}: {d} done, {s} slipped, {u} upcoming")

    return "\n".join(lines)


async def generate_weekly_review(session: Session) -> str:
    """Generate a natural language weekly review.

    Results are cached for 60 minutes.
    Falls back to plain text stats if LLM is unavailable.
    """
    from deadline_agent.config import settings
    from deadline_agent.extraction.extractor import ExtractionError
    from deadline_agent.reasoning.cache import snapshot_hash, weekly_review_cache

    stats = compute_weekly_stats(session)
    stats_text = _stats_to_prompt(stats)

    if stats["done_count"] == 0 and stats["slipped_count"] == 0:
        return "No task activity this week."

    # Return cached result if stats haven't changed
    cache_key = f"weekly:{snapshot_hash(stats_text)}"
    cached = weekly_review_cache.get(cache_key)
    if cached is not None:
        logger.debug("Returning cached weekly review")
        return cached

    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"review": {"type": "string"}},
        "required": ["review"],
    }

    result: str | None = None
    try:
        result = await _call_ollama(WEEKLY_REVIEW_PROMPT, stats_text, schema)
    except ExtractionError:
        if settings.use_anthropic_fallback and settings.anthropic_api_key:
            try:
                result = await _call_anthropic(WEEKLY_REVIEW_PROMPT, stats_text)
            except ExtractionError:
                pass

    if result is None:
        result = f"Weekly Review\n\n{stats_text}"

    weekly_review_cache.set(cache_key, result)
    return result
