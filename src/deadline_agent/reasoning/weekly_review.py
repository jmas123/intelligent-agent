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

    # Phase 25: Recruiting stats
    recruiting_stats: dict[str, Any] = {}
    try:
        from deadline_agent.behavioral.recruiting_report import compute_recruiting_stats

        recruiting_stats = compute_recruiting_stats(session)
    except Exception:
        pass

    return {
        "done_count": len(done),
        "slipped_count": len(slipped),
        "upcoming_count": len(upcoming),
        "by_course": by_course,
        "done_tasks": [t.title for t in done],
        "slipped_tasks": [t.title for t in slipped],
        "recruiting": recruiting_stats,
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

    # Phase 25: Recruiting stats
    recruiting = stats.get("recruiting", {})
    if recruiting and recruiting.get("active_count", 0) > 0:
        lines.append("")
        lines.append("Recruiting pipeline:")
        lines.append(f"  Active applications: {recruiting.get('active_count', 0)}")
        by_status = recruiting.get("by_status", {})
        if by_status:
            parts = [f"{s}: {c}" for s, c in by_status.items() if c > 0]
            lines.append(f"  Status: {', '.join(parts)}")
        rate = recruiting.get("response_rate")
        if rate is not None:
            lines.append(f"  Response rate: {rate:.0%}")
        new_this_week = recruiting.get("new_this_week", 0)
        if new_this_week:
            lines.append(f"  New this week: {new_this_week}")
        changes = recruiting.get("status_changes_this_week", 0)
        if changes:
            lines.append(f"  Status changes this week: {changes}")

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


async def persist_weekly_snapshot(session: Session) -> Any:
    """Compute, narrate, and persist the current week's snapshot.

    Called by the weekly review scheduler to store durable weekly summaries
    for narrative continuity and longitudinal reasoning.
    """
    import json as _json

    from deadline_agent.models import FileActivity, WorkSession
    from deadline_agent.store.snapshot_repository import WeeklySnapshotRepository

    now = datetime.now(UTC)

    # Determine week boundaries (Monday-Sunday)
    days_since_monday = now.weekday()
    monday = (now - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    sunday = monday + timedelta(days=6)
    week_start = monday.strftime("%Y-%m-%d")
    week_end = sunday.strftime("%Y-%m-%d")

    # Compute stats
    stats = compute_weekly_stats(session)

    # Sum work session minutes for the week
    week_ago = monday.isoformat()
    work_sessions = list(
        session.scalars(
            select(WorkSession).where(WorkSession.started_at >= week_ago)
        ).all()
    )
    total_work_minutes = sum(ws.duration_minutes for ws in work_sessions)

    # Capture active life contexts
    life_contexts_data: list[dict[str, str]] = []
    try:
        from deadline_agent.store.context_repository import LifeContextRepository

        ctx_repo = LifeContextRepository(session)
        for ctx in ctx_repo.get_active():
            life_contexts_data.append({
                "season": ctx.season,
                "label": ctx.label or "",
            })
    except Exception:
        pass

    # Health signals
    health_signals: dict[str, object] = {}
    try:
        recent_ts = list(
            session.scalars(
                select(FileActivity.modified_at).where(
                    FileActivity.created_at >= week_ago
                )
            ).all()
        )
        from zoneinfo import ZoneInfo

        _est = ZoneInfo("America/New_York")
        late_dates: set[str] = set()
        for ts in recent_ts:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            local = ts.astimezone(_est)
            if 1 <= local.hour <= 4:
                late_dates.add(local.strftime("%Y-%m-%d"))
        health_signals = {"late_night_days": len(late_dates)}
    except Exception:
        pass

    # Sleep inference
    try:
        from deadline_agent.awareness.physical_inference import infer_sleep_signals

        sleep_signals = infer_sleep_signals(session)
        health_signals.update(sleep_signals)
    except Exception:
        pass

    # Absence detection
    try:
        from deadline_agent.awareness.absence_detector import detect_absences

        absence_signals = detect_absences(session)
        if absence_signals:
            health_signals["absence_signals"] = absence_signals
    except Exception:
        pass

    # Determine semester week number
    semester_week: int | None = None
    try:
        from deadline_agent.reasoning.context_window import _get_current_semester_week

        semester_week = _get_current_semester_week(session)
    except Exception:
        pass

    # Generate narrative
    narrative = await generate_weekly_review(session)

    # Persist
    repo = WeeklySnapshotRepository(session)
    snapshot = repo.upsert(
        week_start,
        {
            "week_end": week_end,
            "tasks_completed": stats["done_count"],
            "tasks_slipped": stats["slipped_count"],
            "tasks_upcoming": stats["upcoming_count"],
            "total_work_minutes": total_work_minutes,
            "stats_json": _json.dumps({
                "by_course": stats.get("by_course", {}),
                "recruiting": stats.get("recruiting", {}),
            }),
            "narrative": narrative,
            "life_contexts_json": _json.dumps(life_contexts_data),
            "health_signals_json": _json.dumps(health_signals),
            "semester_week_number": semester_week,
        },
    )

    logger.info("Persisted weekly snapshot for %s", week_start)
    return snapshot
