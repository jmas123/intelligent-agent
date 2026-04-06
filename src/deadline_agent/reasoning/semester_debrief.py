"""Semester debrief: analytics aggregation + LLM narrative retrospective."""

import json
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from deadline_agent.models import (
    FileActivity,
    FileTaskLink,
    Task,
    WorkSession,
)
from deadline_agent.reasoning.scheduler import EFFORT_MAP

logger = logging.getLogger(__name__)

DEBRIEF_SYSTEM_PROMPT = (
    "You are an academic productivity coach writing an honest end-of-semester "
    "retrospective for a student. Given their semester stats, write a narrative "
    "debrief (5-8 sentences). Include:\n"
    "- Overall accomplishment rate and what it means\n"
    "- Which courses were smooth vs which caused crunch\n"
    "- Effort estimation accuracy — are they underestimating?\n"
    "- Procrastination patterns — are they starting earlier or later?\n"
    "- One concrete habit to change next semester\n\n"
    "Be encouraging but honest. Use specific numbers. Don't sugarcoat."
)


def compute_semester_stats(
    session: Session, start: str, end: str
) -> dict[str, Any]:
    """Compute comprehensive stats for a semester date range."""
    # Tasks completed during the semester
    done = list(
        session.scalars(
            select(Task)
            .where(Task.status == "done")
            .where(Task.created_at >= start)
            .where(Task.created_at <= end)
        ).all()
    )

    # Tasks that slipped (created in semester, still pending, past due)
    slipped = list(
        session.scalars(
            select(Task)
            .where(Task.status == "pending")
            .where(Task.created_at >= start)
            .where(Task.created_at <= end)
            .where(Task.due_date_iso < end)
            .where(Task.due_date_iso.is_not(None))
        ).all()
    )

    # All tasks in the semester
    all_tasks = list(
        session.scalars(
            select(Task)
            .where(Task.created_at >= start)
            .where(Task.created_at <= end)
        ).all()
    )

    # Work sessions in the semester
    total_work_min = (
        session.execute(
            select(func.coalesce(func.sum(WorkSession.duration_minutes), 0))
            .where(WorkSession.started_at >= start)
            .where(WorkSession.started_at <= end)
        ).scalar()
        or 0
    )

    # By course breakdown
    by_course: dict[str, dict[str, int]] = {}
    for t in all_tasks:
        c = t.course or "Other"
        by_course.setdefault(c, {"total": 0, "done": 0, "slipped": 0})
        by_course[c]["total"] += 1
    for t in done:
        c = t.course or "Other"
        by_course.setdefault(c, {"total": 0, "done": 0, "slipped": 0})
        by_course[c]["done"] += 1
    for t in slipped:
        c = t.course or "Other"
        by_course.setdefault(c, {"total": 0, "done": 0, "slipped": 0})
        by_course[c]["slipped"] += 1

    # Lead time report: avg days between task posted and work started
    lead_time = _compute_lead_time(session, start, end)

    # Effort accuracy: estimated vs actual hours
    effort_accuracy = _compute_effort_accuracy(session, start, end)

    # Crunch analysis: last-minute work spikes by course
    crunch = _compute_crunch_analysis(session, done + slipped)

    # Workload distribution: tasks completed per week
    workload_dist = _compute_workload_distribution(done, start, end)

    # Procrastination trend
    procrastination = _compute_procrastination_trend(session, start, end)

    return {
        "tasks_completed": len(done),
        "tasks_slipped": len(slipped),
        "tasks_total": len(all_tasks),
        "total_work_hours": round(total_work_min / 60, 1),
        "total_work_minutes": total_work_min,
        "by_course": by_course,
        "lead_time_by_course": lead_time,
        "effort_accuracy_by_type": effort_accuracy,
        "crunch_by_course": crunch,
        "workload_distribution": workload_dist,
        "procrastination_trend": procrastination,
    }


def _compute_lead_time(
    session: Session, start: str, end: str
) -> dict[str, float]:
    """Average days between task posted and work started, per course."""
    stmt = (
        select(Task)
        .where(Task.created_at >= start)
        .where(Task.created_at <= end)
        .where(Task.id.in_(select(FileTaskLink.task_id).distinct()))
    )
    tasks = list(session.scalars(stmt).all())

    by_course: dict[str, list[float]] = defaultdict(list)
    for task in tasks:
        earliest_stmt = (
            select(FileActivity.modified_at)
            .join(
                FileTaskLink,
                FileTaskLink.file_activity_id == FileActivity.id,
            )
            .where(FileTaskLink.task_id == task.id)
            .order_by(FileActivity.modified_at.asc())
            .limit(1)
        )
        first_activity = session.execute(earliest_stmt).scalar()
        if first_activity is None:
            continue
        lead_days = (first_activity - task.created_at).total_seconds() / 86400
        course = task.course or "Other"
        by_course[course].append(max(0, lead_days))

    return {
        course: round(sum(days) / len(days), 1)
        for course, days in by_course.items()
        if days
    }


def _compute_effort_accuracy(
    session: Session, start: str, end: str
) -> dict[str, dict[str, float]]:
    """Estimated vs actual hours, per task type."""
    stmt = (
        select(Task)
        .where(Task.status == "done")
        .where(Task.created_at >= start)
        .where(Task.created_at <= end)
        .where(Task.id.in_(select(WorkSession.task_id).distinct()))
    )
    done_tasks = list(session.scalars(stmt).all())

    by_type: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for task in done_tasks:
        actual = int(
            session.execute(
                select(
                    func.coalesce(
                        func.sum(WorkSession.duration_minutes), 0
                    )
                ).where(WorkSession.task_id == task.id)
            ).scalar()
        )
        if actual == 0:
            continue
        base = EFFORT_MAP.get(task.type, 90)
        multiplier = 1.0 + (task.urgency_score - 3) * 0.1
        estimated = max(30, int(base * multiplier))
        by_type[task.type].append((actual, estimated))

    result: dict[str, dict[str, float]] = {}
    for task_type, pairs in by_type.items():
        n = len(pairs)
        mean_actual = sum(a for a, _ in pairs) / n
        mean_estimated = sum(e for _, e in pairs) / n
        ratio = mean_actual / mean_estimated if mean_estimated > 0 else 1.0
        result[task_type] = {
            "estimated_hours": round(mean_estimated / 60, 1),
            "actual_hours": round(mean_actual / 60, 1),
            "ratio": round(ratio, 2),
            "sample_count": n,
        }
    return result


def _compute_crunch_analysis(
    session: Session, tasks: list[Task]
) -> dict[str, dict[str, Any]]:
    """Which courses caused last-minute work spikes."""
    by_course: dict[str, list[float]] = defaultdict(list)
    for task in tasks:
        if task.due_date_iso is None:
            continue
        # Find earliest work session
        earliest_stmt = (
            select(WorkSession.started_at)
            .where(WorkSession.task_id == task.id)
            .order_by(WorkSession.started_at.asc())
            .limit(1)
        )
        first_session = session.execute(earliest_stmt).scalar()
        if first_session is None:
            continue
        try:
            due = datetime.fromisoformat(task.due_date_iso)
        except ValueError:
            continue
        days_before = (due - first_session).total_seconds() / 86400
        course = task.course or "Other"
        by_course[course].append(days_before)

    result: dict[str, dict[str, Any]] = {}
    for course, days_list in by_course.items():
        n = len(days_list)
        avg_days = sum(days_list) / n
        last_minute = sum(1 for d in days_list if d < 1)
        result[course] = {
            "avg_days_before_deadline": round(avg_days, 1),
            "last_minute_count": last_minute,
            "total_tasks": n,
            "crunch_ratio": round(last_minute / n, 2) if n else 0,
        }
    return result


def _compute_workload_distribution(
    done_tasks: list[Task], start: str, end: str
) -> list[dict[str, Any]]:
    """Tasks completed per week across the semester."""
    start_dt = datetime.fromisoformat(start)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=UTC)
    end_dt = datetime.fromisoformat(end)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=UTC)

    weeks: list[dict[str, Any]] = []
    cursor = start_dt
    week_num = 1
    while cursor < end_dt:
        week_end = cursor + timedelta(days=7)
        count = sum(
            1
            for t in done_tasks
            if t.updated_at
            and cursor <= t.updated_at.replace(tzinfo=UTC) < week_end
        )
        weeks.append(
            {
                "week": week_num,
                "start": cursor.strftime("%b %d"),
                "tasks_completed": count,
            }
        )
        cursor = week_end
        week_num += 1
    return weeks


def _compute_procrastination_trend(
    session: Session, start: str, end: str
) -> dict[str, Any]:
    """Are you starting earlier or later over time?

    Splits the semester in half and compares average lead times.
    """
    start_dt = datetime.fromisoformat(start)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=UTC)
    end_dt = datetime.fromisoformat(end)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=UTC)
    midpoint = start_dt + (end_dt - start_dt) / 2

    def _avg_lead(range_start: str, range_end: str) -> float | None:
        stmt = (
            select(Task)
            .where(Task.due_date_iso.is_not(None))
            .where(Task.created_at >= range_start)
            .where(Task.created_at <= range_end)
            .where(
                Task.id.in_(select(WorkSession.task_id).distinct())
            )
        )
        tasks = list(session.scalars(stmt).all())
        if not tasks:
            return None
        leads: list[float] = []
        for task in tasks:
            first = session.execute(
                select(WorkSession.started_at)
                .where(WorkSession.task_id == task.id)
                .order_by(WorkSession.started_at.asc())
                .limit(1)
            ).scalar()
            if first is None or task.due_date_iso is None:
                continue
            try:
                due = datetime.fromisoformat(task.due_date_iso)
            except ValueError:
                continue
            leads.append((due - first).total_seconds() / 86400)
        return round(sum(leads) / len(leads), 1) if leads else None

    first_half = _avg_lead(start, midpoint.isoformat())
    second_half = _avg_lead(midpoint.isoformat(), end)

    trend = "stable"
    if first_half is not None and second_half is not None:
        if second_half > first_half + 0.5:
            trend = "improving"
        elif second_half < first_half - 0.5:
            trend = "worsening"

    return {
        "first_half_avg_days": first_half,
        "second_half_avg_days": second_half,
        "trend": trend,
    }


def stats_to_prompt(stats: dict[str, Any]) -> str:
    """Format semester stats as text for LLM input."""
    lines = [
        f"Tasks completed: {stats['tasks_completed']}",
        f"Tasks slipped: {stats['tasks_slipped']}",
        f"Total tasks: {stats['tasks_total']}",
        f"Total work: {stats['total_work_hours']} hours",
        "",
    ]

    by_course = stats.get("by_course", {})
    if by_course:
        lines.append("BY COURSE:")
        for course, data in by_course.items():
            d = data.get("done", 0)
            s = data.get("slipped", 0)
            total = data.get("total", 0)
            lines.append(f"  {course}: {d}/{total} done, {s} slipped")
        lines.append("")

    lead = stats.get("lead_time_by_course", {})
    if lead:
        lines.append("LEAD TIME (avg days from posted to work started):")
        for course, days in lead.items():
            lines.append(f"  {course}: {days} days")
        lines.append("")

    effort = stats.get("effort_accuracy_by_type", {})
    if effort:
        lines.append("EFFORT ACCURACY (estimated vs actual):")
        for ttype, data in effort.items():
            est = data.get("estimated_hours", 0)
            act = data.get("actual_hours", 0)
            ratio = data.get("ratio", 1.0)
            lines.append(
                f"  {ttype}: est {est}h, actual {act}h "
                f"({ratio:.1f}x)"
            )
        lines.append("")

    crunch = stats.get("crunch_by_course", {})
    if crunch:
        lines.append("CRUNCH ANALYSIS:")
        for course, data in crunch.items():
            avg = data.get("avg_days_before_deadline", 0)
            lm = data.get("last_minute_count", 0)
            total = data.get("total_tasks", 0)
            lines.append(
                f"  {course}: avg {avg} days before deadline, "
                f"{lm}/{total} last-minute"
            )
        lines.append("")

    workload = stats.get("workload_distribution", [])
    if workload:
        lines.append("WORKLOAD BY WEEK:")
        for w in workload:
            bar = "#" * w["tasks_completed"]
            lines.append(
                f"  Week {w['week']} ({w['start']}): "
                f"{w['tasks_completed']} {bar}"
            )
        lines.append("")

    proc = stats.get("procrastination_trend", {})
    if proc.get("first_half_avg_days") is not None:
        lines.append("PROCRASTINATION TREND:")
        lines.append(
            f"  First half: started {proc['first_half_avg_days']} "
            f"days before deadline"
        )
        lines.append(
            f"  Second half: started {proc['second_half_avg_days']} "
            f"days before deadline"
        )
        lines.append(f"  Trend: {proc['trend']}")

    return "\n".join(lines)


async def generate_semester_debrief(
    session: Session, start: str, end: str, term_name: str
) -> dict[str, Any]:
    """Compute stats, generate LLM narrative, and store as SemesterRecord.

    Returns {stats, debrief_text, record_id}.
    """
    from deadline_agent.reasoning.engine import call_llm
    from deadline_agent.store.semester_repository import (
        SemesterRecordRepository,
    )

    stats = compute_semester_stats(session, start, end)
    prompt_text = stats_to_prompt(stats)

    # Generate LLM narrative
    debrief_text: str
    try:
        raw = await call_llm(DEBRIEF_SYSTEM_PROMPT, prompt_text)
        # Extract text from JSON if needed
        try:
            parsed = json.loads(raw)
            debrief_text = parsed.get("review", parsed.get("debrief", raw))
        except (json.JSONDecodeError, AttributeError):
            debrief_text = raw
    except Exception:
        logger.warning("LLM debrief generation failed, using plain stats")
        debrief_text = f"Semester Debrief\n\n{prompt_text}"

    # Store as SemesterRecord
    repo = SemesterRecordRepository(session)
    existing = repo.get_by_term(term_name)
    if existing:
        repo.update(
            existing.id,
            {
                "start_date": start,
                "end_date": end,
                "tasks_completed": stats["tasks_completed"],
                "tasks_slipped": stats["tasks_slipped"],
                "total_work_minutes": stats["total_work_minutes"],
                "analytics_json": json.dumps(stats),
                "debrief_text": debrief_text,
            },
        )
        record_id = existing.id
    else:
        record = repo.create(
            {
                "term_name": term_name,
                "start_date": start,
                "end_date": end,
                "tasks_completed": stats["tasks_completed"],
                "tasks_slipped": stats["tasks_slipped"],
                "total_work_minutes": stats["total_work_minutes"],
                "analytics_json": json.dumps(stats),
                "debrief_text": debrief_text,
            }
        )
        record_id = record.id

    return {
        "stats": stats,
        "debrief_text": debrief_text,
        "record_id": record_id,
    }
